import random, re, sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from helpers import make_template
from forgecut.automation.planners import plan_la_voz, plan_got_talent, plan_timeline


def check_common(tc, plan, song):
    cl = plan.clips
    tc.assertAlmostEqual(sum(c.dur_s for c in cl), song, places=5)
    t = 0.0
    for c in cl:
        tc.assertAlmostEqual(c.start_s, t, places=5); t += c.dur_s
    for a, b in zip(cl, cl[1:]):
        tc.assertFalse(a.kind == b.kind == "lipsync", "dos lipsync seguidos")
        tc.assertFalse(a.kind == b.kind == "far", "dos lejanos seguidos")
        if a.kind != "lipsync":
            tc.assertNotEqual(a.path, b.path, "mismo archivo seguido")


def check_voz(tc, plan, song, tpl):
    cl = plan.clips
    turns = [c for c in cl if c.kind == "turn"]
    tc.assertEqual(len(turns), 4)
    tc.assertEqual(len({c.judge for c in turns}), 4)
    tc.assertEqual(len({c.path for c in turns}), 4)
    turn_i = {c.judge: i for i, c in enumerate(cl) if c.kind == "turn"}
    for i, c in enumerate(cl):                                   # estados por juez (por carpeta real)
        m = re.search(r"/j(\d)_(antes|girado)/", c.path or "")
        if m and c.kind == "reaction":
            j, st = int(m.group(1)), m.group(2)
            tc.assertIn(j, turn_i)
            if st == "girado": tc.assertGreater(i, turn_i[j], "girado ANTES de girar")
            if st == "antes":  tc.assertLess(i, turn_i[j], "antes DESPUÉS de girar")
    for a, b in zip(cl, cl[1:]):
        if a.judge and b.judge:
            tc.assertNotEqual(a.judge, b.judge, "mismo juez seguido")
    # los 4 giros caen en la 1ª mitad
    for c in turns:
        tc.assertLess(c.start_s, song / 2)


def intro_end(song, tpl):
    return min(tpl.intro_s, song / 2 * 0.55)


def first_after_intro(plan, song, tpl):
    ie = intro_end(song, tpl) - 0.25
    return next(c for c in plan.clips if c.start_s >= ie)


def max_run_first_half(plan, song, tpl):
    ie, half = intro_end(song, tpl) - 0.25, song / 2
    run = best = 0
    for c in plan.clips:
        if c.start_s < ie or c.start_s >= half: continue
        if c.kind == "lipsync": run = 0
        else: run += 1; best = max(best, run)
    return best


class TestLaVoz(unittest.TestCase):
    def test_invariants_many(self):
        for pools in (dict(), dict(antes=1, girando=1, girado=1, react2=2, far=2), dict(antes=2, girando=1, girado=2, react2=4, far=3)):
            tpl = make_template("la_voz", **pools)
            for song in (150, 180, 240, 300):
                for seed in range(80):
                    plan = plan_la_voz(song, tpl, random.Random(seed))
                    with self.subTest(pools=pools, song=song, seed=seed):
                        check_common(self, plan, song)
                        check_voz(self, plan, song, tpl)

    def test_lipsync_right_after_intro(self):
        tpl = make_template("la_voz")
        for song in (150, 180, 240, 300):
            for seed in range(120):
                plan = plan_la_voz(song, tpl, random.Random(seed))
                with self.subTest(song=song, seed=seed):
                    self.assertEqual(first_after_intro(plan, song, tpl).kind, "lipsync")
                    self.assertFalse(plan.warnings)

    def test_run_of_three_first_half(self):
        tpl = make_template("la_voz")
        bad = tot = 0
        for song in (180, 210, 240, 300):
            for seed in range(150):
                tot += 1
                bad += max_run_first_half(plan_la_voz(song, tpl, random.Random(seed)), song, tpl) > 3
        self.assertLessEqual(bad / tot, 0.02, f"{bad}/{tot} timelines con racha > 3")

    def test_far_duration_param(self):
        for fs in (2.5, 3.0, 5.0):
            tpl = make_template("la_voz", far_s=fs)
            plan = plan_la_voz(240, tpl, random.Random(3))
            check_common(self, plan, 240)
            fars = [c.dur_s for c in plan.clips if c.kind == "far"][:-1]
            self.assertTrue(all(d <= fs + 1e-6 or d == fars[-1] for d in fars[:-1]))

    def test_short_song_warns_not_breaks(self):
        tpl = make_template("la_voz")
        plan = plan_la_voz(100, tpl, random.Random(1))
        check_common(self, plan, 100)
        self.assertEqual(len([c for c in plan.clips if c.kind == "turn"]), 4)

    def test_incomplete_template(self):
        tpl = make_template("la_voz", girando=0)
        with self.assertRaises(ValueError):
            plan_la_voz(180, tpl, random.Random(1))

    def test_deterministic_seed(self):
        tpl = make_template("la_voz")
        a = plan_timeline(200, tpl, seed=7); b = plan_timeline(200, tpl, seed=7)
        self.assertEqual([(c.kind, c.path, round(c.dur_s, 4)) for c in a.clips],
                         [(c.kind, c.path, round(c.dur_s, 4)) for c in b.clips])


class TestGotTalent(unittest.TestCase):
    def test_invariants(self):
        for song in (60, 120, 200, 300):
            for seed in range(60):
                tpl = make_template("got_talent")
                plan = plan_got_talent(song, tpl, random.Random(seed))
                with self.subTest(song=song, seed=seed):
                    check_common(self, plan, song)
                    self.assertGreaterEqual(plan.info["lipsyncs"], 1)
                    for a, b in zip(plan.clips, plan.clips[1:]):
                        if a.judge and b.judge: self.assertNotEqual(a.judge, b.judge)

    def test_intro_has_no_lipsync(self):
        tpl = make_template("got_talent")
        plan = plan_got_talent(300, tpl, random.Random(2))
        self.assertTrue(all(c.kind != "lipsync" for c in plan.clips if c.start_s < 30 * 0.9))


if __name__ == "__main__":
    unittest.main()

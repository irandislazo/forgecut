import hashlib, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from forgecut.automation.templates import Template, PoolItem


def _d(name):
    return 2.0 + (int(hashlib.md5(name.encode()).hexdigest(), 16) % 31) / 10.0     # 2.0 – 5.0 s


def make_template(style="la_voz", antes=3, girando=2, girado=3, react2=8, far=6, react1=6, far_s=3.0):
    t = Template(name="t", style=style, far_s=far_s)
    def add(key, n, prefix):
        for k in range(n):
            nm = f"{prefix}_{k}.mp4"
            t.pool(key).append(PoolItem(path=f"/x/{key}/{nm}", duration_s=_d(nm)))
    add("far", far, "far")
    if style == "la_voz":
        for j in range(1, 5):
            add(f"j{j}_antes", antes, f"j{j}_antes"); add(f"j{j}_girando", girando, f"j{j}_girando")
            add(f"j{j}_girado", girado, f"j{j}_girado")
        add("react2", react2, "r2")
    else:
        add("react1", react1, "r1"); add("react2", react2, "r2")
    return t

<#
  Crea la clave para FIRMAR el APK de release (opcional; sin ella el APK se firma con clave de depuración y se instala igual).
  Guarda el archivo .jks y las contraseñas en un sitio seguro: si lo pierdes no podrás actualizar la app instalada.
  Luego en GitHub → Settings → Secrets and variables → Actions crea estos 4 secretos:
    ANDROID_KEYSTORE_BASE64   (el contenido que imprime este script)
    ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_PASSWORD (la contraseña que pongas), ANDROID_KEY_ALIAS (forgecut)
  Requiere keytool (viene con Java/JDK).
#>
$jks = "forgecut-release.jks"
keytool -genkeypair -v -keystore $jks -alias forgecut -keyalg RSA -keysize 2048 -validity 10000
[Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path $jks))) | Set-Clipboard
Write-Host "`nEl valor de ANDROID_KEYSTORE_BASE64 ya está en el portapapeles (pégalo como secreto)." -ForegroundColor Green

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  SantaLuciaGH - Iniciar servidor local
echo ============================================
if not exist ".env" (
  echo [AVISO] No existe .env - copiando .env.example a .env
  copy ".env.example" ".env" >nul
  echo [AVISO] Edita .env y pon tus claves REALES de Supabase:
  echo   SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY
  echo   Luego vuelve a ejecutar este archivo.
  echo.
  notepad ".env"
  pause
  exit /b 0
)
echo Arrancando en http://127.0.0.1:5000  (Ctrl+C para detener)
echo Prueba los endpoints en:
echo   http://127.0.0.1:5000/api/health
echo   http://127.0.0.1:5000/api/v1/
echo   http://127.0.0.1:5000/api/v1/public/home
echo   http://127.0.0.1:5000/api/v1/products
echo En el navegador: F12 ^> Network ^> Fetch/XHR
echo.
python app.py
pause

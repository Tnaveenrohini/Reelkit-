"""Run WaveRise on your own computer: the website and the Python backend in one program.

  python run_local.py --browser firefox       (uses the Instagram login already saved in Firefox)
  python run_local.py --sessionid YOUR_VALUE  (or paste the sessionid cookie value instead)

Then open the address it prints. Stories, Highlights and profile pictures use your own login and your own
home internet connection, which Instagram trusts far more than a data-centre server."""
import argparse, importlib.util, os, shutil, socket, subprocess, sys

ap = argparse.ArgumentParser(description="Run WaveRise locally")
ap.add_argument("--browser", help="firefox, chrome, edge, brave, opera, vivaldi (Firefox is the most reliable)")
ap.add_argument("--sessionid", help="alternative to --browser: the value of the sessionid cookie")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--no-venv", action="store_true", help="use the current Python instead of a private environment (for Termux or advanced users)")
a = ap.parse_args()

HERE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(HERE, ".venv")
VPY = os.path.join(VENV, "Scripts" if os.name == "nt" else "bin", "python.exe" if os.name == "nt" else "python")
if not (a.no_venv or os.environ.get("WAVERISE_NO_VENV")) and os.path.abspath(sys.prefix) != os.path.abspath(VENV):
    # First run: make a private environment (works even where Python blocks system-wide installs), then restart inside it.
    if not os.path.exists(VPY):
        print("First start: setting things up (about a minute, needs internet)...")
        import venv
        venv.create(VENV, with_pip=True)
    subprocess.check_call([VPY, "-m", "pip", "install", "-q", "-r", os.path.join(HERE, "requirements-local.txt")])
    sys.exit(subprocess.call([VPY, os.path.abspath(__file__)] + sys.argv[1:]))
missing = [m for m in ("fastapi", "uvicorn", "yt_dlp", "httpx") if importlib.util.find_spec(m) is None]
if missing:
    sys.exit("Missing packages: " + ", ".join(missing) + "\nInstall them with:  pip install -r requirements-local.txt")

if a.browser:
    os.environ["IG_BROWSER"] = a.browser.strip().lower()
if a.sessionid:
    os.environ["IG_SESSIONID"] = a.sessionid.strip()
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "your-computer-ip"


login = f"from {a.browser}" if a.browser else "from the sessionid you gave" if a.sessionid else "NONE (Stories and Highlights will not work)"
print(f"""
  WaveRise is starting.

  Open on this computer : http://localhost:{a.port}
  Open on your phone    : http://{lan_ip()}:{a.port}   (same Wi-Fi)
  Instagram login       : {login}
  Check the connection  : open the site, scroll down, tap Status, then Run check

  Keep this window open. Press Ctrl+C to stop.
""")
import uvicorn  # noqa: E402
uvicorn.run("app:app", host="0.0.0.0", port=a.port, log_level="warning")

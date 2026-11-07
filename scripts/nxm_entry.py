"""Windows protocol entry point with project-local src path."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nexus_tab_installer.platform.nxm_handler import main
if __name__ == "__main__":
    raise SystemExit(main())

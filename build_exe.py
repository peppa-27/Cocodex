import subprocess
import sys
from pathlib import Path


def main():
    project_dir = Path(__file__).resolve().parent
    main_file = project_dir / "main.py"
    assets_dir = project_dir / "resources"
    icon_file = project_dir / "icon.ico"

    command = [
        sys.executable,
        "-m",
        "PyInstaller",

        str(main_file),

        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",

        "--name", "CocoDex",
        "--icon", str(icon_file),

        # Windows 和其他系统都可以使用 os.pathsep
        "--add-data", f"{assets_dir}{Path.pathsep if False else ';'}assets",
    ]

    print("开始打包 CocoDex...")
    subprocess.run(
        command,
        cwd=project_dir,
        check=True,
    )

    print("\n打包完成：")
    print(project_dir / "dist" / "CocoDex")


if __name__ == "__main__":
    main()
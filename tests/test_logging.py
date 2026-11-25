import subprocess
import sys


def test_configure_console_logging():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging\n"
            "from duet.core.config import Config\n"
            "from duet.core.logging import configure_logging\n"
            "configure_logging(Config(log_level='WARNING'))\n"
            "logger = logging.getLogger('duet.test')\n"
            "logger.info('hidden message')\n"
            "logger.warning('visible message')\n",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "WARNING duet.test visible message" in result.stderr
    assert "hidden message" not in result.stderr
    assert result.stdout == ""

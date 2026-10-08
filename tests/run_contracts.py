"""Run BotCore contracts in temporary state without touching runtime logs or credentials."""

import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest

source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source))
sys.dont_write_bytecode = True
original_cwd = Path.cwd()
for key in list(os.environ):
    if key.startswith(("MON_", "MONCORE_", "MONBOT_", "NAPCAT_")):
        del os.environ[key]
with tempfile.TemporaryDirectory(prefix="qqbot-contracts-") as temporary:
    state = Path(temporary)
    os.chdir(state)
    (state / ".monconfig").write_text("[log]\nLEVEL=CRITICAL\n", encoding="utf-8")
    (state / "bot-config.json").write_text("{}", encoding="utf-8")
    os.environ.update(MON_LOG_ROOT=str(state / "Logs"), MON_LOG_START_DIR=str(state / "Logs" / "start"),
                      MON_QQBOT_STATE_DIR=str(state / "State"), MON_BOT_CONFIG_FILE=str(state / "bot-config.json"))
    try:
        suite = (unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1 else
                 unittest.defaultTestLoader.discover(str(source / "tests")))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        logging.shutdown()
        os.chdir(original_cwd)
raise SystemExit(not result.wasSuccessful())

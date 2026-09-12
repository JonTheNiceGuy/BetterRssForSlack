import json
import logging

from app.logging_setup import configure_logging


def test_configure_logging_emits_json(capsys):
    configure_logging("INFO")
    logger = logging.getLogger("test.logger")
    logger.info("hello world")

    captured = capsys.readouterr()
    line = captured.err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["levelname"] == "INFO"


def test_configure_logging_respects_level(capsys):
    configure_logging("WARNING")
    logger = logging.getLogger("test.logger.level")
    logger.info("should not appear")
    logger.warning("should appear")

    captured = capsys.readouterr()
    assert "should not appear" not in captured.err
    assert "should appear" in captured.err

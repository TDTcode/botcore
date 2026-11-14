import logging

from .. import log_init

LOG = logging.getLogger("discord.test_log_init")


def written(logfile):
    for handler in logging.getLogger("discord").handlers:
        handler.flush()
    return logfile.read_text(encoding="utf-8")


def test_info_lines_reach_the_log_file_and_the_console_by_default(tmp_path, capsys):
    # "We have logged in", "Reissue detected", "Reboot complete." ... are all info lines
    logfile = tmp_path / "bot.log"
    log_init.init_logging(logfile=str(logfile))
    LOG.info("Reboot complete.")
    LOG.debug("too chatty")
    assert ":INFO:discord.test_log_init: Reboot complete." in written(logfile)
    assert "too chatty" not in written(logfile)
    assert "Reboot complete." in capsys.readouterr().err


def test_the_level_can_be_raised_again(tmp_path, capsys):
    logfile = tmp_path / "bot.log"
    log_init.init_logging(logfile=str(logfile), visual_log_level=logging.PRINTV)
    LOG.info("hidden")
    LOG.log(logging.PRINTV, "shown")
    LOG.warning("also shown")
    text = written(logfile)
    assert "hidden" not in text and "shown\n" in text and "also shown" in text
    assert "hidden" not in capsys.readouterr().err


def test_the_level_does_not_depend_on_the_root_logger(tmp_path):
    # logging.basicConfig is a no-op once the root logger has handlers (e.g. under pytest, or an embedding app)
    root = logging.getLogger()
    assert root.handlers
    old = root.level
    root.setLevel(logging.ERROR)
    try:
        logfile = tmp_path / "bot.log"
        log_init.init_logging(logfile=str(logfile))
        LOG.info("still logged")
        assert "still logged" in written(logfile)
    finally:
        root.setLevel(old)


def test_verbose_console_uses_the_long_format(tmp_path, capsys):
    log_init.init_logging(logfile=str(tmp_path / "bot.log"), verbose=True)
    LOG.info("hello")
    assert "INFO:discord.test_log_init: hello" in capsys.readouterr().err

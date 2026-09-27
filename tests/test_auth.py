import auth
import token_finder


def test_looks_like_token_valid():
    token = "a" * 24 + "." + "b" * 6 + "." + "c" * 30
    assert auth.looks_like_token(token)


def test_looks_like_token_mfa():
    assert auth.looks_like_token("mfa." + "x" * 84)


def test_looks_like_token_invalid():
    assert not auth.looks_like_token("short")
    assert not auth.looks_like_token("")
    assert not auth.looks_like_token(None)
    assert not auth.looks_like_token("x" * 60)  # long enough, but no dot structure


def test_env_token_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "mfa." + "x" * 84)
    monkeypatch.setattr(auth, "TOKEN_FILE", str(tmp_path / "missing.txt"))
    token = auth.get_user_token()
    assert token == "mfa." + "x" * 84


def test_token_file_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    token_file = tmp_path / "token.txt"
    token_file.write_text("a" * 24 + "." + "b" * 6 + "." + "c" * 30 + "\n")
    monkeypatch.setattr(auth, "TOKEN_FILE", str(token_file))
    assert auth.get_user_token() is not None


def test_prompted_token(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_FILE", "/nonexistent/token.txt")
    monkeypatch.setattr(auth.getpass, "getpass", lambda _prompt: "mfa." + "x" * 84)
    assert auth.get_user_token() == "mfa." + "x" * 84


def test_prompted_token_rejects_garbage_then_accepts(monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setattr(auth, "TOKEN_FILE", "/nonexistent/token.txt")
    answers = iter(["garbage", "mfa." + "x" * 84])
    monkeypatch.setattr(auth.getpass, "getpass", lambda _prompt: next(answers))
    assert auth.get_user_token() == "mfa." + "x" * 84


def test_validate_token_format():
    assert token_finder.validate_token_format("mfa." + "x" * 84)
    assert token_finder.validate_token_format("a" * 24 + "." + "b" * 6 + "." + "c" * 30)
    assert not token_finder.validate_token_format("short")
    assert not token_finder.validate_token_format(None)


def test_extract_tokens_from_file(tmp_path):
    leveldb = tmp_path / "leveldb"
    leveldb.mkdir()
    good = "a" * 24 + "." + "b" * 6 + "." + "c" * 30
    (leveldb / "000003.log").write_text(f"junk around {good} more junk")
    tokens = token_finder.extract_tokens_from_path(str(leveldb))
    assert good in tokens


def test_extract_tokens_skips_non_data_files(tmp_path):
    leveldb = tmp_path / "leveldb"
    leveldb.mkdir()
    good = "a" * 24 + "." + "b" * 6 + "." + "c" * 30
    (leveldb / "IGNORE.txt").write_text(good)  # wrong suffix
    assert token_finder.extract_tokens_from_path(str(leveldb)) == []

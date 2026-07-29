"""Unit tests for the pure config loader (`soap.config`)."""

from pathlib import Path

from soap.config import Config, config_path, load_config


def test_absent_file_defaults(tmp_path: Path):
    assert load_config(tmp_path) == Config(always_review=False)


def test_present_true(tmp_path: Path):
    config_path(tmp_path).write_text("always_review: true\n")
    assert load_config(tmp_path).always_review is True


def test_present_false(tmp_path: Path):
    config_path(tmp_path).write_text("always_review: false\n")
    assert load_config(tmp_path).always_review is False


def test_absent_key_defaults_false(tmp_path: Path):
    # A config that exists but omits the key falls back to the default.
    config_path(tmp_path).write_text("something_else: 1\n")
    assert load_config(tmp_path).always_review is False


def test_empty_file_defaults(tmp_path: Path):
    config_path(tmp_path).write_text("")
    assert load_config(tmp_path) == Config()


def test_malformed_yaml_defaults(tmp_path: Path):
    # Broken YAML must not raise — it degrades to defaults.
    config_path(tmp_path).write_text("always_review: [unclosed\n:::")
    assert load_config(tmp_path) == Config()


def test_non_mapping_yaml_defaults(tmp_path: Path):
    # A scalar/list document (not a mapping) also degrades to defaults.
    config_path(tmp_path).write_text("- just\n- a\n- list\n")
    assert load_config(tmp_path) == Config()


def test_truthy_coercion(tmp_path: Path):
    # A non-bool value is coerced sensibly rather than crashing.
    config_path(tmp_path).write_text("always_review: yes\n")  # YAML -> True
    assert load_config(tmp_path).always_review is True

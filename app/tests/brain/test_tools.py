from zara.brain import tools as T


def test_schemas_have_core_tools():
    names = T.schema_names()
    assert {"run_shell", "file_manager", "os_control", "inspect_screen"} <= names


def test_anthropic_format():
    at = T.to_anthropic_tools()
    assert all(set(t) == {"name", "description", "input_schema"} for t in at)


def test_ollama_format():
    ot = T.to_ollama_tools()
    assert all(t["type"] == "function" and "function" in t for t in ot)
    assert ot[0]["function"]["name"] == "run_shell"


def test_gemini_format():
    gt = T.to_gemini_tools()
    assert "function_declarations" in gt[0]
    assert gt[0]["function_declarations"][0]["name"] == "run_shell"


def test_forbidden_guardrail():
    assert T.is_command_forbidden("mkfs.ext4 /dev/sda1")
    assert T.is_command_forbidden("rm -rf /")
    assert T.is_command_forbidden(":(){ :|:& };:")
    assert T.is_command_forbidden("ls -la") is None


def test_file_manager_roundtrip(tmp_path):
    f = tmp_path / "nota.txt"
    assert T.file_manager("write", str(f), content="hola")["success"]
    assert T.file_manager("read", str(f))["content"] == "hola"
    assert T.file_manager("edit", str(f), old_str="hola", new_str="adios")["success"]
    assert T.file_manager("read", str(f))["content"] == "adios"
    assert T.file_manager("exists", str(f))["exists"] is True


def test_file_manager_edit_missing_fragment(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("contenido")
    res = T.file_manager("edit", str(f), old_str="no-esta", new_str="z")
    assert res["success"] is False

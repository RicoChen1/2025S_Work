import textfsm, pathlib, sys
template = pathlib.Path("templates/link-group_set_protect-run-mode.textfsm").read_text()
with open("sample_cmd.txt") as f:
    fsm = textfsm.TextFSM(io.StringIO(template))
    result = fsm.ParseText(f.read())
    print(fsm.header)   # 字段名
    print(result)       # 抽取的值

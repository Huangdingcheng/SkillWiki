# SkillWiki agent package

Three focused agent instructions expose the native SkillWiki CLI workflow:

| Skill | Capability |
|---|---|
| `skillwiki-ingest` | Import sources and review candidates |
| `skillwiki-verify` | Native local verification and identity-bound evidence |
| `skillwiki-manage` | Asset inspection, provenance, execution and governed versions |

Install the backend CLI with `pip install -e "./skillwiki[api]"`, start the server,
and follow the [user guide](../USER_GUIDE.md). For Claude Code, install this plugin
folder using your client's plugin mechanism; for Codex, place or link the three
`skills/*` directories into your configured project skill directory. These are
CLI instruction assets; the main execution harness remains `local_skillwiki`.

Verification defaults to one attempt with repair and promotion disabled. Release
uses its own current-identity evidence workflow. Model-backed operations use your
configured provider and can incur charges. Request examples are available in
[user-guide-examples](../user-guide-examples/README.md).

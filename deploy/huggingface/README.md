---
title: SkillWiki
emoji: 📚
colorFrom: blue
colorTo: cyan
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# SkillWiki Demo

SkillWiki turns source material into reusable skills and connects generation,
execution, verification and Git-backed lifecycle management in one interface.

Open **Knowledge Import**, choose a source type, paste an authored example,
and select **Parse for Candidate Review**. Review the generated candidate
before creating it. Use the skill detail page to execute it, inspect the
recorded output, and run verification. Git snapshots and lifecycle actions
provide an explicit record of the selected skill and version.

The store starts empty. Model-backed actions use the configured provider and
can incur charges. Generation, successful verification and publication are
separate steps; the interface displays their recorded state.

- [User guide](USER_GUIDE.md)
- [Small request examples](user-guide-examples/README.md)
- [Maintainer setup and acceptance](deploy/huggingface/HANDOFF.md)
- [Source project](https://github.com/Huangdingcheng/SkillWiki)

This deployment uses one shared demo store. Its initial acceptance deployment
is intended for a **private Space** with trusted reviewers. Public anonymous
multiuser operation requires a separate access and resource policy.

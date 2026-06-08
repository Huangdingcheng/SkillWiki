Draw a wide two-column ACL/EMNLP overview figure for the SkillWiki paper.

Critical background constraints:
- The entire background outside all panels must be exactly RGB(255,255,255), pure white.
- Outside panel contents, do not use any gray.
- No shadows.
- No gradients.
- No textures.
- No paper effect.
- No drop shadow.
- No outer black frame.
- Flat vector only.

Figure role:
Overall framework of SkillWiki. The figure should be richer than a simple pipeline, but cleaner and less chaotic than a dense graph. It must show two autonomous agent frameworks and the closed-loop skill lifecycle.

Canvas:
- Wide figure* layout, approximately 0.95 textwidth by 3.5 inches.
- Aspect ratio around 2.6:1 to 3:1.
- Pure white background.
- Tight margins.
- No figure title and no caption inside the image.

Style:
- Clean hand-drawn academic cartoon, but organized.
- Use flat colors only.
- Use colored outlines and white panel interiors.
- Allowed colors only: teal, cobalt blue, lavender, emerald, amber, coral, black text, white background.
- Do not use gray for lines, labels, icons, fills, or separators.
- No decorative texture.
- No shaded boxes.
- All text horizontal and readable.

Layout:
Use a structured three-zone architecture with two horizontal agent lanes.

Left zone: "Knowledge Layer"
A vertical stack of six compact source cards:
trajectories, documents, API specs, scripts, past skills, agent executions.
These feed into the construction pipeline.

Middle zone: "SkillWiki Core"
This is the largest central area.
Inside it, draw three stacked subpanels:
1. "Knowledge-Grounded Skill Construction"
   mini pipeline: extract -> normalize -> construct -> audit
2. "Skill Assets"
   show skill card schema with interface, implementation, evaluation, provenance
3. "Provenance Graph"
   show a clean source -> skill -> validation -> version chain plus a few dependency edges

Right zone: "Governed Skill Ecosystem"
Show organized versioned skill cards and governance artifacts:
snapshot, diff, release, rollback.
Also show lifecycle states:
S1 Candidate -> S2 Draft -> S3 Verified -> S4 Released -> S5 Degraded -> S6 Deprecated.

Top horizontal lane:
"Task Execution Agents"
Place five connected agent cards above SkillWiki Core:
Retriever -> Planner -> Executor -> Verifier -> Reflection.
This lane reads from S4 Released skills and outputs "Execution feedback".

Bottom horizontal lane:
"Self-Management Agents"
Place five connected agent cards below SkillWiki Core:
Builder -> Auditor -> Monitor -> Maintainer -> Librarian.
This lane consumes execution feedback and produces "Maintenance proposal" and "Repaired skill version".

Feedback loop:
Draw one clean large loop:
Task Execution Agents -> Execution feedback -> Self-Management Agents -> Git-style governance -> SkillWiki Core.
Keep this loop simple, not crossing through many boxes.

Required exact in-figure text:
- Knowledge Layer
- trajectories
- documents
- API specs
- scripts
- past skills
- agent executions
- SkillWiki Core
- Knowledge-Grounded Skill Construction
- extract
- normalize
- construct
- audit
- Skill Assets
- interface
- implementation
- evaluation
- provenance
- Provenance Graph
- Task Execution Agents
- Retriever
- Planner
- Executor
- Verifier
- Reflection
- Self-Management Agents
- Builder
- Auditor
- Monitor
- Maintainer
- Librarian
- Execution feedback
- Maintenance proposal
- Git-style governance
- Snapshot
- Diff
- Release
- Rollback
- Repaired skill version
- Governed Skill Ecosystem
- S1 Candidate
- S2 Draft
- S3 Verified
- S4 Released
- S5 Degraded
- S6 Deprecated

Important cleanup requirements:
- Minimize crossing arrows.
- Keep modules aligned to a grid.
- Make agent lanes clearly separate from the SkillWiki Core.
- Make arrows purposeful and sparse.
- Do not draw random loose graph lines.
- Do not use gray anywhere except black text if needed; prefer colored outlines.
- Use white fills for cards and panels.

Avoid:
gray background, gray dividers, gray shadows, gray icons, gradients, paper texture, drop shadows, dense tangled arrows, random graph clutter, tiny illegible text, vertical text, rotated text, photorealism, 3D, watermarks, logos, corporate dashboard style.

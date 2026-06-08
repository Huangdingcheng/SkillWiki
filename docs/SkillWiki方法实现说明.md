# SkillWiki 方法实现说明

  ## From Knowledge to Skills

  ### Knowledge Sources

  SkillWiki 的输入不是单一文档库，而是持续进入系统的知识原料流。当前实现中，/ingest 支持五类可解析原料：trajectory、document、
  api_doc、script、past_skills。此外，候选 Skill 创建还支持 agent_execution，用于把 agent 执行后的轨迹、失败、反思和经验重新送回
  Skill 生产链。

  这些原料不会被直接发布成 Skill。系统先把它们作为 evidence source 保留，再生成结构化经验单元 StructuredExperience，其中包含
  raw_content、extracted_actions、normalized_actions、summary、proposed_skill_name、confidence、index_keywords 和 metadata。这样
  Skill 的生成始终有来源证据，而不是凭空生成 prompt。

  对应实现主要在：

  skillwiki/skillwiki/api/routes/ingest.py
  skillwiki/skillwiki/layers/input_knowledge/pipeline.py

  ### Knowledge-Grounded Skill Construction

  Skill 构造由 ExperiencePipeline 完成，本质是一条从知识原料到候选能力资产的生产线：

  raw knowledge source
    -> ExtractorAgent
    -> NormalizerAgent
    -> SummarizerAgent
    -> Ctx2SkillLiteExtractor
    -> IndexerAgent
    -> S1 Candidate Skill

  ExtractorAgent 从原料中抽取可复用 action。NormalizerAgent 把 action 规范化为 verb/object/condition/description 等结构。
  SummarizerAgent 生成候选 Skill 的描述、类型和标签。Ctx2SkillLiteExtractor 进一步生成 context pack、challenge tasks、rubric、
  judge/replay evidence、candidate interface、candidate implementation 和 candidate evaluation。IndexerAgent 生成检索关键词和
  embedding hint。

  生成的 Skill 默认进入 S1 Skill Candidate，而不是直接发布。候选 Skill 会包含输入输出 schema、preconditions、postconditions、
  prompt/code/tool/sub-skill 实现、verifier specs、source provenance 和 graph relations。系统随后调用 auditor 做 schema、安全、
  postcondition 和实现完整性检查。

  Skill 的表征不是普通 prompt，而是一个完整 capability object：

  Skill =
    identity
    + classification
    + lifecycle
    + interface
    + implementation
    + evaluation
    + provenance
    + graph relations
    + runtime metrics

  核心模型在：

  skillwiki/skillwiki/models/skill_model.py

  ### Skill Provenance Graph

  SkillWiki 不只保存 Skill 本身，还把 Skill 的来源、构造、验证和版本关系写入图。候选 Skill 创建后，系统同步 Skill-only graph 和
  heterogeneous provenance graph。

  异构 provenance graph 的主链是：

  Source -> Skill -> Execution -> Validation -> Version

  Source 表示原始知识来源，Skill 表示生成的候选能力，Execution 表示候选创建和审计事件，Validation 表示 verifier/audit evidence，
  Version 表示当前 Skill snapshot 版本。对于组合型 Skill，系统还会把 sub_skill_ids、component_ids、parent_skill_ids 映射为图关
  系。

  这个设计让 demo 可以追问一个 Skill 的完整来源链：它来自什么原料、经过什么构造过程、有哪些验证证据、当前处于哪个版本、依赖或组
  合了哪些 Skill。

  ———

  ## Managing Skills at Scale

  ### Organizing Skills

  SkillWiki 通过结构化 Skill schema、生命周期状态、类型层级、标签、领域、图关系和检索排序来组织大量 Skill。

  Skill 类型分为三层：

  atomic      原子操作
  functional  可复用功能单元
  strategic   生命周期、生成、维护、质量保证、知识管理等元技能

  生命周期使用 S0-S7：

  S0 Raw Experience
  S1 Skill Candidate
  S2 Draft
  S3 Verified
  S4 Released
  S5 Degraded
  S6 Deprecated
  S7 Archived

  检索层支持 lexical 和 hybrid 两种模式。lexical 会综合名称、描述、标签、domain、状态和成功率打分。hybrid 在 lexical 基础上加入
  本地 deterministic hash embedding 和 health score：

  score = lexical_score * 0.5 + semantic_score * 0.4 + health_score * 0.1

  因此，Skill 不是按文件夹静态堆放，而是按类型、生命周期、健康度、语义相关性和图关系组织成可查询、可组合、可治理的资产库。

  ### Governing Skills

  SkillWiki 的治理层采用 Git-style lifecycle governance。Skill 变更不是直接覆盖数据库字段，而是生成稳定 JSON snapshot、
  structured diff、review bundle、release tag 和 restore commit。

  治理对象包括：

  skills/{skill_id}/{version}.json

  snapshot 中保存 Skill 的核心字段，包括 interface、implementation、evaluation、provenance、dependency_ids 和 component_ids。
  diff 层会比较 schema、实现、依赖、evaluation、provenance 等字段，并识别 breaking changes，例如输入输出 schema 变化、实现删除、
  sub-skill 顺序变化等。

  治理流程可以概括为：

  proposal
    -> patched_skill
    -> snapshot
    -> structured diff
    -> breaking-change detection
    -> review branch / commit
    -> release tag
    -> rollback / restore commit

  这使 Skill 演化具备可审计性。论文里应强调：系统支持 agent 自主提出变更，但不绕过治理层直接修改 live Skill；最终合入是 review-
  gated 的。

  ### Evolving Skills

  Skill 演化由 runtime metrics、health monitor、reflection memory 和 maintenance proposal 共同驱动。

  每次 Skill 执行后，record_execution() 会更新 usage_count、success_count、failure_count、success_rate、avg_latency_ms 和最近使
  用时间。Monitor 根据这些指标判断 Skill 是否 healthy、degraded、critical 或 stale。

  当执行失败或反思记录重复出现时，系统会把 reflection memory 聚类。当前实现中，相同 failure signature 达到阈值后会生成
  maintenance proposal。proposal 可建议 repair、split、merge、deprecate 等动作，并包含 evidence、root cause、patch hint、
  validation plan 和 invariants to preserve。

  演化闭环是：

  execution failure
    -> reflection memory
    -> repeated failure cluster
    -> maintenance proposal
    -> accepted proposal
    -> lifecycle governance endpoint
    -> snapshot / diff / review bundle
    -> repaired or new Skill version

  因此 SkillWiki 的 scale management 不只是“管理很多 Skill”，而是让 Skill 库具备持续维护能力：使用中的失败会回流为新原料，重复问
  题会变成治理 proposal，proposal 再通过 Git-style lifecycle 产生可审计的新版本。
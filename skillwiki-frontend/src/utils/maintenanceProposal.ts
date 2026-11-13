import type { MaintenanceProposal, SkillFull, SkillImplementation, SkillInterface } from '../api/types'

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record).sort().map(key => `${JSON.stringify(key)}:${stable(record[key])}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

// Match the backend artifact projection; mutable usage/proof is not content.
export function maintenanceParentContent(skill: SkillFull) {
  const implementation: Record<string, unknown> | null = skill.implementation ? { ...skill.implementation } : null
  if (implementation?.prompt_format == null) delete implementation?.prompt_format
  if (implementation?.request_body_validation == null) delete implementation?.request_body_validation
  return {
    skill_id: skill.skill_id, version: skill.version, name: skill.name,
    description: skill.description, interface: skill.interface, implementation,
    evaluation: {
      verifier_specs: skill.evaluation.verifier_specs,
      test_case_refs: skill.evaluation.test_case_refs,
      benchmark_task_ids: skill.evaluation.benchmark_task_ids,
    },
  }
}

export function acceptedRepairPatch(proposal: MaintenanceProposal, parent: SkillFull) {
  if (proposal.status !== 'accepted') throw new Error('Accept this proposal in Evolution before reviewing its patch.')
  if (proposal.skill_id !== parent.skill_id || proposal.metadata.parent_version !== parent.version) {
    throw new Error('This proposal belongs to a different parent Skill or version.')
  }
  if (!proposal.metadata.parent_content || stable(proposal.metadata.parent_content) !== stable(maintenanceParentContent(parent))) {
    throw new Error('The parent content has changed. Generate a fresh repair proposal before applying this patch.')
  }
  const patch = proposal.metadata.patched_skill
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) {
    throw new Error('This proposal has no saved repair patch. Review its recommended action in Evolution.')
  }
  const fields = patch as Record<string, unknown>
  if (!fields.interface || !fields.implementation || Object.keys(fields).some(key => !['interface', 'implementation'].includes(key))) {
    throw new Error('The saved repair patch has unsupported or missing fields.')
  }
  return { interface: fields.interface as SkillInterface, implementation: fields.implementation as SkillImplementation }
}

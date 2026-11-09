import type { SkillRollbackRecord } from '../api/types'

export function recoveryDraftId(response: SkillRollbackRecord) {
  if (response.draft_created !== true || response.live_skill_updated !== false
    || !response.restored_skill_id || response.restored_skill_id === response.skill_id
    || response.restored_state !== 'S2') {
    throw new Error('Backend did not confirm a separate S2 recovery draft. Inspect the response before continuing.')
  }
  return response.restored_skill_id
}

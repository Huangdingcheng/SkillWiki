export function skillIdFromSearch(search: string | URLSearchParams) {
  return new URLSearchParams(search).get('skill_id')?.trim() || null
}

export function searchWithSkillId(search: string | URLSearchParams, skillId: string | null) {
  const next = new URLSearchParams(search)
  if (skillId) next.set('skill_id', skillId)
  else next.delete('skill_id')
  return next
}

export function skillContextPath(path: string, skillId?: string | null) {
  return skillId ? `${path}?${new URLSearchParams({ skill_id: skillId })}` : path
}

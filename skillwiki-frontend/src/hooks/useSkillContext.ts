import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { searchWithSkillId, skillIdFromSearch } from '@/utils/skillContext'
export { skillContextPath } from '@/utils/skillContext'

export function useSkillContext() {
  const [searchParams, setSearchParams] = useSearchParams()
  const skillId = skillIdFromSearch(searchParams)
  const setSkillId = useCallback((id: string | null) => {
    setSearchParams(previous => searchWithSkillId(previous, id), { replace: true })
  }, [setSearchParams])
  return { skillId, setSkillId }
}

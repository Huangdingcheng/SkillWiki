import type { ExecutionResult } from '../api/types'

export function executionEvidence(result: ExecutionResult) {
  const executionPassed = result.status === 'success'
  const executionFailed = result.status === 'failed' || result.status === 'error'
  const verifierPassed = result.verifier_passed === true
  const verifierFailed = result.verifier_passed === false
  const experienceRecorded = result.experience_recorded === true
  const alertType = executionFailed || verifierFailed ? 'error'
    : executionPassed && verifierPassed ? 'success' : 'warning'
  return {
    alertType: alertType as 'error' | 'success' | 'warning',
    experienceRecorded,
    verificationLabel: verifierPassed ? 'Verifier passed' : verifierFailed ? 'Verifier failed' : 'Verification not confirmed',
    verificationColor: verifierPassed ? 'green' : verifierFailed ? 'red' : 'default',
    steps: [
      { title: 'Execution', description: `Backend status: ${result.status}`, status: executionPassed ? 'finish' as const : executionFailed ? 'error' as const : 'wait' as const },
      { title: 'Experience Recording', description: experienceRecorded ? 'Backend confirmed recording' : 'Recording not confirmed', status: experienceRecorded ? 'finish' as const : 'wait' as const },
      { title: 'Verification', description: verifierPassed ? 'Backend verifier passed' : verifierFailed ? 'Backend verifier failed' : 'No confirmed verifier result', status: verifierPassed ? 'finish' as const : verifierFailed ? 'error' as const : 'wait' as const },
    ],
  }
}

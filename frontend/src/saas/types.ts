export type WorkspaceChoice = { tenantId: string; name: string; kind: 'HOSPITAL' | 'INDIVIDUAL'; roles: string[] }
export type ActiveWorkspace = WorkspaceChoice & { canSign: boolean; canUseAi: boolean }
export type Session = { saas?: boolean; demoMode?: boolean; platformAdmin?: boolean; userId?: string; workspace?: ActiveWorkspace | null; workspaces?: WorkspaceChoice[] }
export type Lease = { token: string; fence: number; editingSession: string; expiresAt: string; version: number }

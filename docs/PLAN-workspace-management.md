# Workspace & Project Management Interface

## Overview
Add a dedicated "Settings" view to manage workspaces and projects, displaying git status and worktree information.

## Requirements
1. Separate screen (new view mode)
2. Add/remove workspaces
3. Rescan workspace for new projects
4. Add/remove projects
5. Show current git branch and git status
6. Show open worktrees

## Existing Backend Support
All CRUD operations already implemented:
- `GET/POST/DELETE /api/workspaces`
- `POST /api/workspaces/{id}/scan`
- `GET/POST/DELETE /api/projects`
- `GET /api/projects/{id}` (includes git status)

## Implementation Plan

### Phase 1: Settings Page Foundation
- [ ] Add 'settings' to ViewMode type in App.tsx
- [ ] Add Settings/Gear icon to header view toggle
- [ ] Create `SettingsPage.tsx` component with tab navigation
- [ ] Add workspace and project tabs

### Phase 2: Workspace Management
- [ ] Create `WorkspaceList.tsx` - list all workspaces with project counts
- [ ] Create `WorkspaceCard.tsx` - individual workspace display with actions
- [ ] Add "Rescan" button per workspace (calls POST /api/workspaces/{id}/scan)
- [ ] Create `AddWorkspaceDialog.tsx` - form to add new workspace
- [ ] Add delete workspace functionality with confirmation

### Phase 3: Project Management
- [ ] Create `ProjectList.tsx` - list all projects grouped by workspace
- [ ] Create `ProjectCard.tsx` - individual project with git info
- [ ] Display git branch, uncommitted changes, commits ahead/behind
- [ ] Create `AddProjectDialog.tsx` - form to add standalone project
- [ ] Add delete project functionality with confirmation

### Phase 4: Git Status Enhancement
- [ ] Add new API endpoint `GET /api/projects/{id}/git-status` for live git info
- [ ] Add backend function to fetch live git status (branch, status, worktrees)
- [ ] Display active worktrees per project
- [ ] Show worktree branch and path information

### Phase 5: Polish
- [ ] Add loading states for all async operations
- [ ] Add error handling and toast notifications
- [ ] Responsive design for mobile
- [ ] Keyboard navigation support

## UI Layout

```
┌─────────────────────────────────────────────────────────┐
│ GLUON  [All Projects ▼]         [Board][Usage][⚙️] [+]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Settings                                               │
│  ─────────────────────────────────────────────────────  │
│  [Workspaces] [Projects]                                │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 📁 carrotly           /Users/mcutler/workspaces │   │
│  │    3 projects                    [Rescan] [×]   │   │
│  │    ├── gluon-agent     main ● 2 uncommitted     │   │
│  │    ├── web-app         feature/auth ↑3 ↓1      │   │
│  │    └── api-service     main ✓ clean            │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 📁 personal           /Users/mcutler/personal   │   │
│  │    1 project                     [Rescan] [×]   │   │
│  │    └── dotfiles        main ✓ clean            │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  [+ Add Workspace]                                      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Git Status Display
- Branch name with icon
- `●` = uncommitted changes (with count)
- `↑N` = commits ahead of remote
- `↓N` = commits behind remote
- `✓` = clean working tree
- Worktree indicator if project has active worktrees

## Files to Create
1. `web-ui/src/components/SettingsPage.tsx` - Main settings container
2. `web-ui/src/components/WorkspaceList.tsx` - Workspace management
3. `web-ui/src/components/WorkspaceCard.tsx` - Individual workspace
4. `web-ui/src/components/ProjectCard.tsx` - Project with git status
5. `web-ui/src/components/AddWorkspaceDialog.tsx` - Add workspace form
6. `web-ui/src/components/AddProjectDialog.tsx` - Add project form

## Files to Modify
1. `web-ui/src/App.tsx` - Add settings view mode and gear icon
2. `web-ui/src/lib/api.ts` - Add missing API calls if needed
3. `src/gluon/web/api.py` - Add live git status endpoint

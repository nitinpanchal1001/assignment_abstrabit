import { EmptyState } from './ui';

/** Shown when a signed-in user has no workspace at all. */
export function EmptyWorkspaces() {
  return (
    <div className="px-6 py-20">
      <EmptyState
        title="No workspace yet"
        description="Create your first workspace from the switcher at the top of the sidebar. Documents, chat and tool activity are all scoped to a workspace."
      />
    </div>
  );
}

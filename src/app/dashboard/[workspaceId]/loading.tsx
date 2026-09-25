import { LoadingBlock } from '@/components/ui';

/**
 * Shared fallback for Overview, Documents, Tasks and Activity — every page in
 * this segment renders a `PageHeader` over a padded body, so one shape fits
 * all four. Chat has its own, because its layout is full-height.
 *
 * The sidebar stays mounted and interactive underneath: only the page is
 * suspended, so a mis-click can be corrected without waiting for this to
 * finish.
 */
export default function Loading() {
  return <LoadingBlock className="min-h-[70vh]" />;
}

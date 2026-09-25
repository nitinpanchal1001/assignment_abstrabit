import { LoadingBlock } from '@/components/ui';

/** Covers `/dashboard` while it resolves the first workspace and redirects. */
export default function Loading() {
  return <LoadingBlock label="Opening your workspace" className="min-h-screen" />;
}

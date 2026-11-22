import Link from 'next/link';

export default function HomePage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-3xl font-bold tracking-tight">StruggleTime Docs</h1>
      <p className="max-w-md text-[var(--color-fd-muted-foreground)]">
        Setup, tray, daemon, API, and privacy documentation.
      </p>
      <Link
        href="/docs/"
        className="rounded-lg bg-[var(--color-fd-primary)] px-4 py-2 font-semibold text-[var(--color-fd-primary-foreground)]"
      >
        Open the docs
      </Link>
    </main>
  );
}

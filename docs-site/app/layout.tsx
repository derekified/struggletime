import { Provider } from '@/components/provider';
import './global.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: {
    default: 'StruggleTime Docs',
    template: '%s · StruggleTime Docs',
  },
  description: 'Setup, tray, daemon, API, and privacy docs for StruggleTime.',
  icons: {
    icon: [{ url: '/img/struggletime.svg', type: 'image/svg+xml' }],
    apple: [{ url: '/img/struggletime.svg' }],
  },
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className="dark docs-app" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;650;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="flex flex-col min-h-screen docs-app">
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}

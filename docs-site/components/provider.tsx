'use client';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { type ReactNode } from 'react';
import { DocsThemeSync } from '@/components/docs-theme-sync';

export function Provider({ children }: { children: ReactNode }) {
  return (
    <RootProvider
      theme={{ defaultTheme: 'dark', enabled: false }}
      search={{ enabled: false }}
    >
      <DocsThemeSync />
      {children}
    </RootProvider>
  );
}

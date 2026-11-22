import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { gitConfig } from './shared';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: <span className="text-[var(--st-ink)]">Docs</span>,
    },
    githubUrl: `https://${gitConfig.host || 'github.com'}/${gitConfig.user}/${gitConfig.repo}`,
    themeSwitch: {
      enabled: false,
    },
    slots: {
      themeSwitch: false,
    },
    links: [
      {
        text: 'Dashboard',
        url: '/home',
        active: 'none',
      },
      {
        text: 'Install',
        url: '/docs/installation',
        active: 'nested-url',
      },
      {
        text: 'Tray',
        url: '/docs/tray',
        active: 'nested-url',
      },
      {
        text: 'Privacy',
        url: '/docs/privacy',
        active: 'nested-url',
      },
    ],
  };
}

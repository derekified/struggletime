import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { appName, gitConfig } from './shared';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: appName,
    },
    githubUrl: `https://origin.cursor.com/${gitConfig.user}/${gitConfig.repo}`,
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

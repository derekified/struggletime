import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { appName, gitConfig } from './shared';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <span className="inline-flex items-center gap-2 text-[var(--st-ink)]">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 128 128"
            fill="none"
            stroke="currentColor"
            width="22"
            height="22"
            className="text-[var(--st-accent)]"
            aria-hidden="true"
          >
            <circle cx="64" cy="64" r="48" strokeWidth="8" />
            <path d="M64 24V30M64 98V104M24 64H30M98 64H104" strokeWidth="5" />
            <path
              d="M64 64C59 59 54 53 48 53C43.5 53 41 56 41 59.5C41 65.5 53 65 53 72.5C53 76.5 49.5 79.5 45 79.5C40.5 79.5 37.5 77 35.5 74"
              strokeWidth="6.5"
            />
            <path d="M64 64L86 42" strokeWidth="6.5" />
            <path d="M77 33L95 51" strokeWidth="6.5" />
            <circle cx="64" cy="64" r="4" fill="currentColor" stroke="none" />
          </svg>
          <span>{appName}</span>
        </span>
      ),
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

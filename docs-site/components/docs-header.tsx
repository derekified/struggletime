'use client';

import { useDocsLayout } from 'fumadocs-ui/layouts/docs';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { Menu } from 'lucide-react';
import type { ComponentProps } from 'react';

function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ');
}

/** Mobile docs header — opens the shared sidebar with a plain menu icon. */
export function DocsHeader(props: ComponentProps<'header'>) {
  const {
    isNavTransparent,
    slots,
    props: { nav },
  } = useDocsLayout();

  if (nav?.component) return nav.component;

  return (
    <header
      id="nd-subnav"
      data-transparent={isNavTransparent}
      {...props}
      className={cn(
        '[grid-area:header] sticky top-(--fd-docs-row-1) z-40 flex items-center gap-2 ps-4 pe-2.5 border-b h-(--fd-header-height) md:hidden max-md:layout:[--fd-header-height:--spacing(14)] data-[transparent=false]:bg-fd-background/80',
        props.className,
      )}
    >
      {slots.navTitle && (
        <slots.navTitle className="inline-flex items-center gap-2.5 font-semibold min-w-0" />
      )}
      <div className="flex-1">{nav?.children}</div>
      {slots.searchTrigger && (
        <slots.searchTrigger.sm hideIfDisabled className="p-2" />
      )}
      {slots.sidebar && (
        <slots.sidebar.trigger
          className={cn(
            buttonVariants({
              variant: 'ghost',
              size: 'icon-sm',
              className: 'p-2 text-fd-foreground',
            }),
          )}
          aria-label="Open menu"
        >
          <Menu className="size-5" aria-hidden="true" />
        </slots.sidebar.trigger>
      )}
    </header>
  );
}

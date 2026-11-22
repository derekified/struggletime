'use client';

/**
 * One sidebar for desktop + mobile: the PC `#nd-sidebar` body.
 * On narrow viewports it opens as a fixed drawer (no separate mobile tree UI).
 */
import {
  SidebarFolder,
  SidebarFolderContent,
  SidebarFolderLink,
  SidebarFolderTrigger,
  SidebarItem,
  SidebarProvider as BaseSidebarProvider,
  SidebarSeparator,
  SidebarTrigger as BaseSidebarTrigger,
  SidebarViewport,
  useFolder,
  useFolderDepth,
  useSidebar,
} from 'fumadocs-ui/components/sidebar/base';
import { createPageTreeRenderer } from 'fumadocs-ui/components/sidebar/page-tree';
import { createLinkItemRenderer } from 'fumadocs-ui/components/sidebar/link-item';
import { SidebarTabsDropdown } from 'fumadocs-ui/components/sidebar/tabs/dropdown';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { useDocsLayout } from 'fumadocs-ui/layouts/docs';
import Link from 'fumadocs-core/link';
import { Languages, Menu } from 'lucide-react';
import type { ComponentProps, ReactNode } from 'react';
import { cva } from 'class-variance-authority';

function cn(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ');
}

const itemVariants = cva(
  'relative flex flex-row items-center gap-2 rounded-lg p-2 text-start text-fd-muted-foreground wrap-anywhere [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        link: 'data-[active=true]:bg-fd-primary/10 data-[active=true]:text-fd-primary',
        button: '',
      },
      highlight: {
        true: "data-[active=true]:before:content-[''] data-[active=true]:before:bg-fd-primary data-[active=true]:before:absolute data-[active=true]:before:w-px data-[active=true]:before:inset-y-2.5 data-[active=true]:before:inset-s-2.5",
      },
    },
  },
);

function getItemOffset(depth: number) {
  return `calc(${2 + 3 * depth} * var(--spacing))`;
}

function STSidebarSeparator({
  className,
  style,
  children,
  ...props
}: ComponentProps<'p'>) {
  const depth = useFolderDepth();
  return (
    <SidebarSeparator
      className={cn(
        'inline-flex items-center gap-2 mb-1 px-2 mt-6 empty:mb-0 [&_svg]:size-4 [&_svg]:shrink-0',
        depth === 0 && 'first:mt-0',
        className,
      )}
      style={{ paddingInlineStart: getItemOffset(depth), ...style }}
      {...props}
    >
      {children}
    </SidebarSeparator>
  );
}

function STSidebarItem({
  className,
  style,
  children,
  ...props
}: ComponentProps<typeof SidebarItem>) {
  const depth = useFolderDepth();
  return (
    <SidebarItem
      className={cn(
        itemVariants({ variant: 'link', highlight: depth >= 1 }),
        className,
      )}
      style={{ paddingInlineStart: getItemOffset(depth), ...style }}
      {...props}
    >
      {children}
    </SidebarItem>
  );
}

function STSidebarFolderTrigger(
  props: ComponentProps<typeof SidebarFolderTrigger>,
) {
  const { depth, collapsible } = useFolder() ?? { depth: 1, collapsible: true };
  return (
    <SidebarFolderTrigger
      {...props}
      className={(state) =>
        cn(
          itemVariants({ variant: collapsible ? 'button' : 'link' }),
          'w-full',
          typeof props.className === 'function'
            ? props.className(state)
            : props.className,
        )
      }
      style={{
        paddingInlineStart: getItemOffset(Math.max(0, depth - 1)),
        ...props.style,
      }}
    />
  );
}

function STSidebarFolderLink({
  className,
  style,
  ...props
}: ComponentProps<typeof SidebarFolderLink>) {
  const depth = useFolderDepth();
  return (
    <SidebarFolderLink
      className={cn(
        itemVariants({ variant: 'link', highlight: depth > 1 }),
        'w-full',
        className,
      )}
      style={{
        paddingInlineStart: getItemOffset(Math.max(0, depth - 1)),
        ...style,
      }}
      {...props}
    />
  );
}

function STSidebarFolderContent({
  className,
  ...props
}: ComponentProps<typeof SidebarFolderContent>) {
  const depth = useFolderDepth();
  return (
    <SidebarFolderContent
      className={(state) =>
        cn(
          'relative flex flex-col gap-0.5 pt-0.5',
          depth === 1 &&
            "before:content-[''] before:absolute before:w-px before:inset-y-1 before:bg-fd-border before:inset-s-2.5",
          typeof className === 'function' ? className(state) : className,
        )
      }
      {...props}
    />
  );
}

const SidebarPageTree = createPageTreeRenderer({
  SidebarFolder,
  SidebarFolderContent: STSidebarFolderContent,
  SidebarFolderLink: STSidebarFolderLink,
  SidebarFolderTrigger: STSidebarFolderTrigger,
  SidebarItem: STSidebarItem,
  SidebarSeparator: STSidebarSeparator,
});

const SidebarLinkItem = createLinkItemRenderer({
  SidebarFolder,
  SidebarFolderContent: STSidebarFolderContent,
  SidebarFolderLink: STSidebarFolderLink,
  SidebarFolderTrigger: STSidebarFolderTrigger,
  SidebarItem: STSidebarItem,
});

export type DocsSidebarProps = ComponentProps<'aside'> & {
  banner?: ReactNode;
  footer?: ReactNode;
  collapsible?: boolean;
  components?: Parameters<typeof SidebarPageTree>[0];
};

export function DocsSidebar({
  banner,
  footer,
  components,
  className,
  ...rest
}: DocsSidebarProps) {
  const {
    menuItems,
    slots,
    props: { tabs, nav, tabMode },
  } = useDocsLayout();
  const { open, setOpen, mode } = useSidebar();
  const iconLinks = menuItems.filter((item) => item.type === 'icon');
  const isDrawer = mode === 'drawer';
  const showMobile = isDrawer && open;

  const viewport = (
    <SidebarViewport>
      {menuItems
        .filter((v) => v.type !== 'icon')
        .map((item, i, list) => (
          <SidebarLinkItem
            key={i}
            item={item}
            className={cn(i === list.length - 1 && 'mb-4')}
          />
        ))}
      <SidebarPageTree {...components} />
    </SidebarViewport>
  );

  const body = (
    <>
      <div className="flex flex-col gap-3 p-4 pb-2">
        <div className="flex items-center gap-2">
          {slots.navTitle && (
            <slots.navTitle className="inline-flex text-[0.9375rem] items-center gap-2.5 font-medium me-auto min-w-0" />
          )}
          {nav?.children}
        </div>
        {slots.searchTrigger && <slots.searchTrigger.full hideIfDisabled />}
        {tabs.length > 0 && (
          <SidebarTabsDropdown
            options={
              tabMode === 'auto'
                ? tabs
                : tabs.filter((tab) => typeof tab.$folder?.root === 'string')
            }
          />
        )}
        {banner}
      </div>
      {viewport}
      {(slots.languageSelect ||
        iconLinks.length > 0 ||
        slots.themeSwitch ||
        footer) && (
        <div className="flex flex-col p-4 pt-2">
          {slots.languageSelect && (
            <slots.languageSelect.root
              variant="secondary"
              className="text-fd-muted-foreground text-start justify-start bg-fd-secondary/50 mb-2"
            >
              <Languages className="size-4.5" />
              <slots.languageSelect.text />
            </slots.languageSelect.root>
          )}
          <div className="flex text-fd-muted-foreground items-center border bg-fd-secondary/50 p-0.5 pe-0 rounded-lg empty:hidden">
            {iconLinks.map((item, i) => (
              <Link
                key={i}
                href={item.url}
                external={item.external}
                className={cn(
                  buttonVariants({ size: 'icon-sm', variant: 'ghost' }),
                )}
                aria-label={item.label}
              >
                {item.icon}
              </Link>
            ))}
            {slots.themeSwitch && (
              <slots.themeSwitch className="px-1 py-0 border-y-0 border-e-0 rounded-none ms-auto *:rounded-md" />
            )}
          </div>
          {footer}
        </div>
      )}
    </>
  );

  return (
    <>
      {showMobile ? (
        <button
          type="button"
          className="docs-sidebar-overlay fixed inset-0 z-40 bg-black/45 md:hidden"
          aria-label="Close sidebar"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <div
        data-sidebar-placeholder=""
        data-mobile-open={showMobile ? 'true' : 'false'}
        className={cn(
          'z-50 pointer-events-none *:pointer-events-auto',
          'md:sticky md:top-(--fd-docs-row-1) md:z-20 md:[grid-area:sidebar]',
          'md:h-[calc(var(--fd-docs-height)-var(--fd-docs-row-1))]',
          'md:layout:[--fd-sidebar-width:var(--st-rail)]',
          'max-md:fixed max-md:inset-y-0 max-md:inset-s-0',
          'max-md:w-[min(100%,18rem)]',
          showMobile ? 'max-md:block' : 'max-md:hidden',
        )}
      >
        <aside
          id="nd-sidebar"
          data-sidebar=""
          data-collapsed="false"
          data-hovered="false"
          className={cn(
            'flex flex-col w-full h-full bg-fd-card text-sm border-e overflow-y-auto',
            'md:absolute md:inset-s-0 md:inset-y-0 md:items-stretch',
            'md:w-(--fd-sidebar-width)',
            'max-md:w-full max-md:max-w-[18rem]',
            className,
          )}
          {...rest}
        >
          {body}
        </aside>
      </div>
    </>
  );
}

export function DocsSidebarProvider(
  props: ComponentProps<typeof BaseSidebarProvider>,
) {
  return <BaseSidebarProvider {...props} />;
}

/** Mobile header control — hamburger, not the panel/collapse icon. */
export function DocsSidebarTrigger({
  children,
  className,
  ...props
}: ComponentProps<'button'>) {
  return (
    <BaseSidebarTrigger
      className={cn(
        buttonVariants({
          variant: 'ghost',
          size: 'icon-sm',
          className: 'p-2 text-fd-foreground',
        }),
        className,
      )}
      {...props}
    >
      {children ?? <Menu className="size-5" aria-hidden="true" />}
    </BaseSidebarTrigger>
  );
}

export { useSidebar as useDocsSidebar };

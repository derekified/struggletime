import { source } from '@/lib/source';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { baseOptions } from '@/lib/layout.shared';
import { DocsHeader } from '@/components/docs-header';
import {
  DocsSidebar,
  DocsSidebarProvider,
  DocsSidebarTrigger,
  useDocsSidebar,
} from '@/components/docs-sidebar';

export default function Layout({ children }: LayoutProps<'/docs'>) {
  const options = baseOptions();
  return (
    <DocsLayout
      tree={source.getPageTree()}
      {...options}
      sidebar={{ collapsible: false }}
      slots={{
        ...options.slots,
        header: DocsHeader,
        sidebar: {
          provider: DocsSidebarProvider,
          root: DocsSidebar,
          trigger: DocsSidebarTrigger,
          useSidebar: useDocsSidebar,
        },
      }}
    >
      {children}
    </DocsLayout>
  );
}

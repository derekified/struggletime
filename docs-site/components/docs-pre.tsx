'use client';

import { CodeBlock, Pre } from 'fumadocs-ui/components/codeblock';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';

export function DocsPre(props: ComponentPropsWithoutRef<'pre'>) {
  const { children, ...rest } = props;
  return (
    <CodeBlock
      {...rest}
      className="st-codeblock"
      viewportProps={{ className: 'st-code-viewport' }}
      Actions={({ children: actions }: { children?: ReactNode }) => (
        <div className="st-code-copy">{actions}</div>
      )}
    >
      <Pre>{children}</Pre>
    </CodeBlock>
  );
}

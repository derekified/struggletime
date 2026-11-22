import defaultMdxComponents from 'fumadocs-ui/mdx';
import type { ComponentPropsWithoutRef } from 'react';
import type { MDXComponents } from 'mdx/types';
import { DocsPre } from './docs-pre';

function Table(props: ComponentPropsWithoutRef<'table'>) {
  return (
    <div className="table-wrap w-full max-w-full min-w-0 overflow-x-auto">
      <table {...props} />
    </div>
  );
}

export function getMDXComponents(components?: MDXComponents) {
  return {
    ...defaultMdxComponents,
    table: Table,
    pre: DocsPre,
    ...components,
  } satisfies MDXComponents;
}

export const useMDXComponents = getMDXComponents;

declare global {
  type MDXProvidedComponents = ReturnType<typeof getMDXComponents>;
}

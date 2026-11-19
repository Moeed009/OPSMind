// @vitest-environment jsdom
import { lazy, Suspense, useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RouteSkeleton } from './App';
import { Button, Dialog, Tabs, TabsContent, TabsList, TabsTrigger } from './components/ui';

describe('Phase 5 Polish & Accessibility', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders RouteSkeleton as Suspense fallback while a lazy route loads', async () => {
    let resolveComponent!: (mod: { default: () => React.ReactElement }) => void;
    const LazyPage = lazy(
      () =>
        new Promise<{ default: () => React.ReactElement }>((resolve) => {
          resolveComponent = resolve;
        }),
    );

    render(
      <Suspense fallback={<RouteSkeleton />}>
        <LazyPage />
      </Suspense>,
    );

    expect(screen.getByRole('status', { name: /Loading page/i })).toBeTruthy();

    resolveComponent({ default: () => <div>Loaded Lazy Route</div> });

    await waitFor(() => {
      expect(screen.getByText('Loaded Lazy Route')).toBeTruthy();
    });
  });

  it('closes Dialog on Escape key press', () => {
    function DemoDialog() {
      const [open, setOpen] = useState(true);
      return (
        <Dialog open={open} onOpenChange={setOpen} title="Keyboard Dialog" description="Press Escape">
          <Button type="button" onClick={() => setOpen(false)}>
            Done
          </Button>
        </Dialog>
      );
    }

    render(<DemoDialog />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toBeTruthy();

    fireEvent.keyDown(dialog, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('supports keyboard ArrowRight navigation on Tabs', async () => {
    const user = userEvent.setup();
    render(
      <Tabs defaultValue="one">
        <TabsList>
          <TabsTrigger value="one">Tab One</TabsTrigger>
          <TabsTrigger value="two">Tab Two</TabsTrigger>
        </TabsList>
        <TabsContent value="one">Panel One</TabsContent>
        <TabsContent value="two">Panel Two</TabsContent>
      </Tabs>,
    );

    const tabOne = screen.getByRole('tab', { name: 'Tab One' });
    const tabTwo = screen.getByRole('tab', { name: 'Tab Two' });
    tabOne.focus();
    await user.keyboard('{ArrowRight}');

    expect(tabTwo.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByText('Panel Two')).toBeTruthy();
  });
});

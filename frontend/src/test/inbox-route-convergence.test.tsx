import fs from 'node:fs';
import path from 'node:path';

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { CanonicalConversationRedirect, LegacyInboxRedirect } from '@/App';

type InboxChannel = 'whatsapp' | 'instagram' | 'messenger';

function LocationProbe() {
  const location = useLocation();

  return (
    <output data-testid="location">
      {JSON.stringify({
        pathname: location.pathname,
        search: location.search,
        hash: location.hash,
      })}
    </output>
  );
}

function renderLegacyInbox(pathname: string, channel: InboxChannel) {
  render(
    <MemoryRouter
      initialEntries={[
        `${pathname}?matter=matter-123&thread=thread-456&status=open&tag=vip&tag=urgent&channel=outdated#message-9`,
      ]}
    >
      <Routes>
        <Route path={pathname} element={<LegacyInboxRedirect channel={channel} />} />
        <Route path="/app/conversas" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe.each([
  ['/whatsapp/inbox', 'whatsapp'],
  ['/instagram/inbox', 'instagram'],
  ['/messenger/inbox', 'messenger'],
] as const)('%s', (legacyPath, channel) => {
  it(`converges on the canonical ${channel} conversation inbox without losing context`, async () => {
    renderLegacyInbox(legacyPath, channel);

    const location = JSON.parse((await screen.findByTestId('location')).textContent ?? '{}') as {
      pathname: string;
      search: string;
      hash: string;
    };
    const searchParams = new URLSearchParams(location.search);

    expect(location.pathname).toBe('/app/conversas');
    expect(searchParams.get('channel')).toBe(channel);
    expect(searchParams.get('matter')).toBe('matter-123');
    expect(searchParams.get('thread')).toBe('thread-456');
    expect(searchParams.get('status')).toBe('open');
    expect(searchParams.getAll('tag')).toEqual(['vip', 'urgent']);
    expect(location.hash).toBe('#message-9');
  });
});

describe('legacy inbox route registration', () => {
  it('uses internal canonical redirects while retaining connection and configuration routes', () => {
    const source = fs.readFileSync(path.resolve(process.cwd(), 'src', 'App.tsx'), 'utf8');

    expect(source).toContain('<Route path="/whatsapp/inbox" element={<LegacyInboxRedirect channel="whatsapp" />} />');
    expect(source).toContain('<Route path="/instagram/inbox" element={<LegacyInboxRedirect channel="instagram" />} />');
    expect(source).toContain('<Route path="/messenger/inbox" element={<LegacyInboxRedirect channel="messenger" />} />');

    expect(source).toContain('path="/whatsapp/connections"');
    expect(source).toContain('path="/whatsapp/templates"');
    expect(source).toContain('path="/whatsapp/automations"');
    expect(source).toContain('path="/instagram/connections"');
    expect(source).toContain('path="/instagram/automation"');
    expect(source).toContain('path="/messenger/connections"');
    expect(source).toContain('path="/messenger/automation"');
    expect(source).not.toMatch(/window\.location|location\.assign|location\.replace/);
  });
});

describe('/app/omnichannel', () => {
  it('converges on Conversas without losing the selected case, thread, filters or anchor', async () => {
    render(
      <MemoryRouter initialEntries={['/app/omnichannel?matter=matter-123&thread=thread-456&channel=instagram&tag=vip#message-9']}>
        <Routes>
          <Route path="/app/omnichannel" element={<CanonicalConversationRedirect />} />
          <Route path="/app/conversas" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    const location = JSON.parse((await screen.findByTestId('location')).textContent ?? '{}') as {
      pathname: string;
      search: string;
      hash: string;
    };
    const searchParams = new URLSearchParams(location.search);

    expect(location.pathname).toBe('/app/conversas');
    expect(searchParams.get('matter')).toBe('matter-123');
    expect(searchParams.get('thread')).toBe('thread-456');
    expect(searchParams.get('channel')).toBe('instagram');
    expect(searchParams.get('tag')).toBe('vip');
    expect(location.hash).toBe('#message-9');
  });
});

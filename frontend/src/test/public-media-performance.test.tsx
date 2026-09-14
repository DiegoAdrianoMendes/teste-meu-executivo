/** @vitest-environment jsdom */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { PublicMediaFrame } from '@/components/public/PublicMediaFrame';

afterEach(cleanup);

describe('PublicMediaFrame performance fallbacks', () => {
  it('keeps the real GIF lazy and asynchronously decoded when no video source is configured', () => {
    render(<PublicMediaFrame media={{ layoutVariant: 'hero' }} />);

    const image = screen.getByRole('img');
    expect(image).toHaveAttribute('src', '/site-media/hero-demo-real.gif');
    expect(image).toHaveAttribute('loading', 'lazy');
    expect(image).toHaveAttribute('decoding', 'async');
    expect(document.querySelector('video')).toBeNull();
  });

  it('uses only explicitly configured WebM and MP4 sources ahead of the GIF fallback', () => {
    const { container } = render(<PublicMediaFrame media={{
      layoutVariant: 'hero',
      webmUrl: '/media/demo.webm',
      mp4Url: '/media/demo.mp4',
    }} />);

    expect(container.querySelector('video')).not.toBeNull();
    expect(container.querySelector('source[type="video/webm"]')).toHaveAttribute('src', '/media/demo.webm');
    expect(container.querySelector('source[type="video/mp4"]')).toHaveAttribute('src', '/media/demo.mp4');
    expect(container.querySelector('img')).toBeNull();
  });
});

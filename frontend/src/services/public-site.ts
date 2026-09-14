import { apiFetch } from '@/services/http';
import type {
  BlogCategoryPayload,
  BlogIndexPayload,
  BlogPost,
  PublicHomePayload,
  PublicLegalDocument,
  PublicSiteSettings,
} from '@/types';

export async function fetchPublicSiteSettings(): Promise<PublicSiteSettings> {
  return apiFetch('/api/v1/public/site');
}

export async function fetchPublicHome(): Promise<PublicHomePayload> {
  return apiFetch('/api/v1/public/home');
}

export async function fetchPublicLegal(kind: 'terms' | 'privacy'): Promise<PublicLegalDocument> {
  return apiFetch(`/api/v1/public/legal/${kind}`);
}

export async function fetchPublicBlogIndex(): Promise<BlogIndexPayload> {
  return apiFetch('/api/v1/public/blog');
}

export async function fetchPublicBlogPost(slug: string): Promise<BlogPost> {
  return apiFetch(`/api/v1/public/blog/${slug}`);
}

export async function fetchPublicBlogCategory(slug: string): Promise<BlogCategoryPayload> {
  return apiFetch(`/api/v1/public/blog/category/${slug}`);
}

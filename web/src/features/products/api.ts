/** Product catalog API. */

import { api } from "@/lib/api/client"
import type { Page } from "@/features/settings/api"

export type Product = {
  id: string
  sku: string
  barcode: string | null
  name: string
  description: string | null
  type: "goods" | "service"
  category_id: string | null
  uom_id: string | null
  is_purchasable: boolean
  is_sellable: boolean
  track_inventory: boolean
  sale_price: string
  cost_price: string
  sale_tax_id: string | null
  purchase_tax_id: string | null
  min_stock: string
  status: string
}

export type ProductInput = {
  name: string
  sku?: string
  barcode?: string | null
  description?: string | null
  type: "goods" | "service"
  category_id?: string | null
  uom_id?: string | null
  is_purchasable?: boolean
  is_sellable?: boolean
  track_inventory?: boolean
  sale_price?: string
  cost_price?: string
  sale_tax_id?: string | null
  purchase_tax_id?: string | null
  min_stock?: string
}

export type Uom = { id: string; code: string; name: string }
export type Tax = {
  id: string
  code: string
  name: string
  rate_pct: string
  applies_to: string
  is_default_sale: boolean
  is_default_purchase: boolean
}
export type Category = { id: string; parent_id: string | null; name: string }

export const catalogApi = {
  products: (params: { q?: string; category_id?: string; type?: string; limit?: number } = {}) => {
    const clean = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][],
    )
    return api.get<Page<Product>>(`/api/catalog/products?${new URLSearchParams(clean)}`)
  },
  product: (id: string) => api.get<Product>(`/api/catalog/products/${id}`),
  generateDescription: (id: string) =>
    api.post<{ description: string; model: string }>(
      `/api/catalog/products/${id}/generate-description`,
      undefined,
    ),
  createProduct: (body: ProductInput) => api.post<Product>("/api/catalog/products", body),
  updateProduct: (id: string, body: Partial<ProductInput>) =>
    api.patch<Product>(`/api/catalog/products/${id}`, body),
  archiveProduct: (id: string, archived: boolean) =>
    api.post<Product>(`/api/catalog/products/${id}/archive?archived=${archived}`, undefined),

  uoms: () => api.get<Uom[]>("/api/catalog/uoms"),
  createUom: (body: { code: string; name: string }) => api.post<Uom>("/api/catalog/uoms", body),
  taxes: () => api.get<Tax[]>("/api/catalog/taxes"),
  createTax: (body: { code: string; name: string; rate_pct: string; applies_to: string }) =>
    api.post<Tax>("/api/catalog/taxes", body),
  categories: () => api.get<Page<Category>>("/api/catalog/categories?limit=100"),
  createCategory: (body: { name: string; parent_id?: string | null }) =>
    api.post<Category>("/api/catalog/categories", body),
  deleteCategory: (id: string) => api.delete<void>(`/api/catalog/categories/${id}`),
}

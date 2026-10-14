# Product images and S3

The pipeline extracts product image URLs from Amazon, Flipkart and Croma product pages. Persisted bulk collection and reconciliation workers upload images before opening the database transaction. Uploads are disabled by default. This implementation does not create a bucket, CDN, IAM credentials or public bucket policy.

## Configuration

Set these in the pipeline's `.env`:

```dotenv
CERTIKART_IMAGE_UPLOAD_ENABLED=false
CERTIKART_IMAGE_S3_BUCKET=
CERTIKART_IMAGE_S3_REGION=ap-south-1
CERTIKART_IMAGE_S3_ENDPOINT_URL=
CERTIKART_IMAGE_S3_FORCE_PATH_STYLE=false
CERTIKART_IMAGE_PUBLIC_BASE_URL=
CERTIKART_IMAGE_S3_ACCESS_KEY_ID=
CERTIKART_IMAGE_S3_SECRET_ACCESS_KEY=
CERTIKART_IMAGE_S3_SESSION_TOKEN=
```

**No CDN is required.** Leave `CERTIKART_IMAGE_PUBLIC_BASE_URL` empty: the uploader generates direct HTTPS S3 URLs from bucket and region, such as `https://your-bucket.s3.ap-south-1.amazonaws.com/products/webp-v1/<hash>.webp`. Dotted bucket names use the regional path-style endpoint for HTTPS compatibility. An explicit base URL remains an optional future override.

These are permanent object URLs, not presigned URLs. The product-image objects must allow browser reads through your chosen S3 access policy; private objects return AccessDenied. This code does not modify bucket permissions or public-access settings. See [AWS S3 URL formats](https://docs.aws.amazon.com/AmazonS3/latest/userguide/VirtualHosting.html).

Supply both access key and secret for local credentials, plus the session token when using temporary credentials. Leave these fields empty to use the SDK's AWS profile/environment/IAM-role credential chain. The uploader needs `s3:PutObject` on its configured object prefix. Direct delivery needs read access to the product-image objects. The uploader never sets a public ACL, lists buckets or creates infrastructure. Credentials stay in the pipeline environment, never `NEXT_PUBLIC_*` variables.

For Next image optimization, optionally set the direct S3 base in the frontend's `.env.local`:

```dotenv
NEXT_PUBLIC_PRODUCT_IMAGE_BASE_URL=https://your-bucket.s3.ap-south-1.amazonaws.com
```

Leaving the frontend image base empty still displays uploaded images without Next resizing. Set `CERTIKART_IMAGE_UPLOAD_ENABLED=true` only after configuring a working bucket and credential source. Restart the pipeline and Next.js after changing configuration; rebuild Next.js for production public environment changes. The backend needs no new S3 variables: it already passes product JSON attributes through the catalog API.

For Supabase Storage, use its S3 compatibility endpoint and public object URL base:

```dotenv
CERTIKART_IMAGE_S3_REGION=your-project-region
CERTIKART_IMAGE_S3_ENDPOINT_URL=https://your-project-ref.storage.supabase.co/storage/v1/s3
CERTIKART_IMAGE_S3_FORCE_PATH_STYLE=true
CERTIKART_IMAGE_PUBLIC_BASE_URL=https://your-project-ref.supabase.co/storage/v1/object/public/product-images
```

The `product-images` bucket must exist and be public for permanent browser-readable product image
URLs. Generated Supabase S3 credentials are server-only and bypass Storage RLS; never expose them to
the frontend.

## Optional limits

| Variable | Default | Meaning |
| --- | --- | --- |
| `CERTIKART_IMAGE_S3_PREFIX` | `products` | Object key prefix |
| `CERTIKART_IMAGE_MAX_BYTES` | `8000000` | Maximum downloaded image bytes |
| `CERTIKART_IMAGE_MAX_DIMENSION` | `1200` | Longest output edge in pixels |
| `CERTIKART_IMAGE_MAX_PER_PRODUCT` | `4` | Maximum images uploaded for one product |
| `CERTIKART_IMAGE_ALLOWED_HOSTS` | Retailer CDN allowlist in `src/config.py` | JSON array of exact download hostnames, if customization is necessary |

## Processing and persistence

- HTTPS image downloads use an exact host allowlist, public DNS checks, no redirects and a byte limit. JPEG, PNG and WebP inputs are decoded and checked against a 25-megapixel limit.
- Images are orientation-corrected, resized without upscaling, stripped of embedded metadata and converted to WebP. Content hashes give deterministic keys; conditional S3 puts avoid overwriting existing objects. Immutable cache headers support CDN caching. URL downloads are deduplicated within each report.
- Product `attributes.image_url` and `attributes.images` store the primary image and gallery, including dimensions, source listing ID, source image URL, observation time, transformation version and content hash. No schema migration is needed. Parsed image URLs stay outside the product fingerprint so images do not change cluster identity.
- The canonical cluster's initial listing supplies its title and gallery; other linked listings do not get mixed into that gallery. Existing matching limitations still apply. Images are collected evidence, not a claim of independently verified variant identity.
- Download/upload failures are logged by product ID and error class without credentials or source URLs. Product data still persists, and previously stored images survive a failed refresh. Re-collection retries failed images. Old objects are not automatically deleted; a failed DB transaction can leave an unreferenced immutable object.
- Existing products need another permitted collection to acquire image URLs and uploads. This is not an automatic backfill of historical rows. The persisted bulk CLI path and reconciliation worker perform uploads; read-only/demo runs do not.
- Reconciliation workers have a 900-second timeout to accommodate image work; split large runs into bounded batches. The SDK work runs in a thread so it does not block the async worker loop.

The frontend displays the complete recorded title or a model-matched retailer title, falling back to descriptive canonical specifications. S3/CDN galleries use responsive Next image optimization, while other source images retain the unoptimized fallback. Missing images remain explicit.

Validation uses stubbed S3 requests and isolated SQLite; it does not establish that an unconfigured AWS bucket is ready.

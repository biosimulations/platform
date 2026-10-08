import type {
  ModelLanguage,
  OmexMetadataInputFormat,
  OmexMetadataSchema,
  ValidateProjectOptions,
  ValidationMessage,
  ValidationReport,
  ValidationStatus
} from '~/models/combine-api';

/**
 * Recursively normalizes raw error or warning payloads from the COMBINE API
 * into a structured array of ValidationMessage objects.
 */
export function normalizeValidationMessages(raw: any): ValidationMessage[] {
  if (!raw) return [];

  const items = Array.isArray(raw) ? raw : [raw];
  const normalized: ValidationMessage[] = [];

  for (const item of items) {
    if (!item) continue;

    if (Array.isArray(item)) {
      normalized.push(...normalizeValidationMessages(item));
      continue;
    }

    if (typeof item === 'string') {
      normalized.push({
        _type: 'ValidationMessage',
        summary: item.trim()
      });
      continue;
    }

    if (typeof item === 'object') {
      const summary = item.summary || item.message || item.description || JSON.stringify(item);
      const details = item.details ? normalizeValidationMessages(item.details) : undefined;
      normalized.push({
        _type: 'ValidationMessage',
        summary: String(summary).trim(),
        details: details && details.length > 0 ? details : undefined
      });
    }
  }

  return normalized;
}

/**
 * Normalizes a raw validation response into a canonical ValidationReport.
 */
export function normalizeValidationReport(raw: any): ValidationReport {
  if (!raw || typeof raw !== 'object') {
    return {
      _type: 'ValidationReport',
      status: 'invalid',
      errors: [{ _type: 'ValidationMessage', summary: 'Empty or invalid response from validation service.' }],
      warnings: []
    };
  }

  const errors = normalizeValidationMessages(raw.errors);
  const warnings = normalizeValidationMessages(raw.warnings);

  let status: ValidationStatus = raw.status;
  if (!status || !['valid', 'warnings', 'invalid'].includes(status)) {
    if (errors.length > 0) {
      status = 'invalid';
    } else if (warnings.length > 0) {
      status = 'warnings';
    } else {
      status = 'valid';
    }
  }

  return {
    _type: 'ValidationReport',
    status,
    errors: errors.length > 0 ? errors : undefined,
    warnings: warnings.length > 0 ? warnings : undefined
  };
}

/**
 * Composable providing client access to the BioSimulations COMBINE API.
 */
export function useCombineApi() {
  const config = useRuntimeConfig();
  // Routed through the Platform API rather than combine.api.biosimulations.org
  // directly. The COMBINE API returns no Access-Control-Allow-Origin header for
  // any origin, and its preflight answers 200 with no Access-Control-* headers
  // at all, so a browser call to it fails everywhere -- production included, not
  // just localhost. The Platform relays it server-side, where CORS does not
  // apply. See backend/biosim_server/validation/router.py.
  const platformApiUrl = (config.public.api_url as string | undefined) ?? '';
  const baseUrl = `${platformApiUrl.replace(/\/+$/, '')}/validation`;
  const combineApiUrl = ((config.public.combine_api_url as string | undefined) || 'https://combine.api.biosimulations.org').replace(/\/+$/, '');

  /**
   * Validate a model file or public URL against the COMBINE API.
   *
   * @param fileOrUrl - Either a File object or a string URL to the model file.
   * @param language - Model language specification (e.g. SBML, CellML, BNGL, etc.).
   */
  async function validateModel(
    fileOrUrl: File | string,
    language: ModelLanguage
  ): Promise<ValidationReport> {
    const formData = new FormData();
    formData.append('language', language);

    if (typeof fileOrUrl === 'string') {
      formData.append('url', fileOrUrl.trim());
    } else {
      formData.append('file', fileOrUrl, fileOrUrl.name);
    }

    try {
      const response = await $fetch<any>(`${baseUrl}/model`, {
        method: 'POST',
        body: formData
      });

      return normalizeValidationReport(response);
    } catch (err: any) {
      // Check for structured HTTPError payload from COMBINE API (e.g. 400 Bad Request)
      const errorData = err?.data;
      if (errorData && (errorData.title || errorData.detail)) {
        return {
          _type: 'ValidationReport',
          status: 'invalid',
          errors: [
            {
              _type: 'ValidationMessage',
              summary: errorData.title || 'Model validation error',
              details: errorData.detail
                ? [{ _type: 'ValidationMessage', summary: errorData.detail }]
                : undefined
            }
          ],
          warnings: []
        };
      }

      // Re-throw unexpected connection or server errors
      throw err;
    }
  }

  /**
   * Validate a SED-ML simulation experiment file or public URL against the COMBINE API.
   *
   * @param fileOrUrl - Either a File object or a string URL to the SED-ML file.
   */
  async function validateSedml(
    fileOrUrl: File | string
  ): Promise<ValidationReport> {
    const formData = new FormData();

    if (typeof fileOrUrl === 'string') {
      formData.append('url', fileOrUrl.trim());
    } else {
      formData.append('file', fileOrUrl, fileOrUrl.name);
    }

    try {
      const response = await $fetch<any>(`${baseUrl}/sed-ml`, {
        method: 'POST',
        body: formData
      });

      return normalizeValidationReport(response);
    } catch (err: any) {
      // Check for structured HTTPError payload from COMBINE API (e.g. 400 Bad Request)
      const errorData = err?.data;
      if (errorData && (errorData.title || errorData.detail)) {
        return {
          _type: 'ValidationReport',
          status: 'invalid',
          errors: [
            {
              _type: 'ValidationMessage',
              summary: errorData.title || 'Simulation validation error',
              details: errorData.detail
                ? [{ _type: 'ValidationMessage', summary: errorData.detail }]
                : undefined
            }
          ],
          warnings: []
        };
      }

      // Re-throw unexpected connection or server errors
      throw err;
    }
  }

  /**
   * Validate an OMEX metadata document file or public URL against the COMBINE API.
   *
   * @param fileOrUrl - Either a File object or a string URL to the metadata file.
   * @param format - Format of the metadata document (rdfxml, turtle, ntriples, nquads, rdfa).
   * @param schema - Schema specification ('BioSimulations' or 'rdf_triples').
   */
  async function validateOmexMetadata(
    fileOrUrl: File | string,
    format: OmexMetadataInputFormat,
    schema: OmexMetadataSchema
  ): Promise<ValidationReport> {
    const formData = new FormData();
    formData.append('format', format);
    formData.append('schema', schema);

    if (typeof fileOrUrl === 'string') {
      formData.append('url', fileOrUrl.trim());
    } else {
      formData.append('file', fileOrUrl, fileOrUrl.name);
    }

    try {
      const response = await $fetch<any>(`${combineApiUrl}/omex-metadata/validate`, {
        method: 'POST',
        body: formData
      });

      return normalizeValidationReport(response);
    } catch (err: any) {
      // Check for structured HTTPError payload from COMBINE API (e.g. 400 Bad Request)
      const errorData = err?.data;
      if (errorData && (errorData.title || errorData.detail)) {
        return {
          _type: 'ValidationReport',
          status: 'invalid',
          errors: [
            {
              _type: 'ValidationMessage',
              summary: errorData.title || 'Metadata validation error',
              details: errorData.detail
                ? [{ _type: 'ValidationMessage', summary: errorData.detail }]
                : undefined
            }
          ],
          warnings: []
        };
      }

      // Re-throw unexpected connection or server errors
      throw err;
    }
  }

  /**
   * Validate a COMBINE / OMEX archive file or public URL against the COMBINE API.
   *
   * @param fileOrUrl - Either a File object or a string URL to the archive.
   * @param options - Granular validation options and metadata settings.
   */
  async function validateProject(
    fileOrUrl: File | string,
    options?: ValidateProjectOptions
  ): Promise<ValidationReport> {
    const formData = new FormData();

    formData.append('omexMetadataFormat', options?.omexMetadataFormat || 'rdfxml');
    formData.append('omexMetadataSchema', options?.omexMetadataSchema || 'BioSimulations');

    if (options?.validateOmexManifest !== undefined) {
      formData.append('validateOmexManifest', String(options.validateOmexManifest));
    }
    if (options?.validateSedml !== undefined) {
      formData.append('validateSedml', String(options.validateSedml));
    }
    if (options?.validateSedmlModels !== undefined) {
      formData.append('validateSedmlModels', String(options.validateSedmlModels));
    }
    if (options?.validateOmexMetadata !== undefined) {
      formData.append('validateOmexMetadata', String(options.validateOmexMetadata));
    }
    if (options?.validateImages !== undefined) {
      formData.append('validateImages', String(options.validateImages));
    }

    if (typeof fileOrUrl === 'string') {
      formData.append('url', fileOrUrl.trim());
    } else {
      formData.append('file', fileOrUrl, fileOrUrl.name);
    }

    const relayEndpoint = baseUrl ? `${baseUrl}/project` : null;
    const directEndpoint = `${combineApiUrl}/combine/validate`;

    async function sendRequest(url: string) {
      return await $fetch<any>(url, {
        method: 'POST',
        body: formData
      });
    }

    try {
      let response: any;
      if (relayEndpoint) {
        try {
          response = await sendRequest(relayEndpoint);
        } catch (relayErr: any) {
          // If the relay is unavailable (404/503), fall back to the direct COMBINE API
          if (relayErr?.status === 404 || relayErr?.status === 503) {
            response = await sendRequest(directEndpoint);
          } else {
            throw relayErr;
          }
        }
      } else {
        response = await sendRequest(directEndpoint);
      }

      return normalizeValidationReport(response);
    } catch (err: any) {
      // Check for structured HTTPError payload from COMBINE API (e.g. 400 Bad Request)
      const errorData = err?.data;
      if (errorData && (errorData.title || errorData.detail || errorData.validationReport)) {
        if (errorData.validationReport) {
          return normalizeValidationReport(errorData.validationReport);
        }
        return {
          _type: 'ValidationReport',
          status: 'invalid',
          errors: [
            {
              _type: 'ValidationMessage',
              summary: errorData.title || 'Project validation error',
              details: errorData.detail
                ? [{ _type: 'ValidationMessage', summary: errorData.detail }]
                : undefined
            }
          ],
          warnings: []
        };
      }

      // Re-throw unexpected connection or server errors
      throw err;
    }
  }

  return {
    baseUrl,
    validateModel,
    validateSedml,
    validateOmexMetadata,
    validateProject
  };
}

import type {
  ModelLanguage,
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
  const baseUrl = config.public.combine_api_url as string;

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
      const response = await $fetch<any>(`${baseUrl}/model/validate`, {
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
      const response = await $fetch<any>(`${baseUrl}/sed-ml/validate`, {
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

  return {
    baseUrl,
    validateModel,
    validateSedml
  };
}

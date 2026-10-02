import { ref } from 'vue';
import type { ValidationReport } from '~/models/combine-api';

/**
 * Composable for managing validation state, executing validation runs,
 * and dispatching standardized user feedback toasts.
 */
export function useValidation() {
  const toast = useToast();

  const isLoading = ref(false);
  const report = ref<ValidationReport | null>(null);
  const error = ref<string | null>(null);

  async function runValidation(
    validationFn: () => Promise<ValidationReport>
  ): Promise<ValidationReport | null> {
    isLoading.value = true;
    error.value = null;
    report.value = null;

    try {
      const res = await validationFn();
      report.value = res;

      if (res.status === 'valid') {
        toast.add({
          title: 'Validation Passed',
          description: 'The model conforms to community specifications with no errors or warnings.',
          color: 'success',
          icon: 'i-lucide-check-circle'
        });
      } else if (res.status === 'warnings') {
        const count = res.warnings?.length || 0;
        toast.add({
          title: 'Validation Passed with Warnings',
          description: `The model is valid but generated ${count} non-fatal warning${count === 1 ? '' : 's'}.`,
          color: 'warning',
          icon: 'i-lucide-alert-triangle'
        });
      } else {
        const count = res.errors?.length || 0;
        toast.add({
          title: 'Validation Failed',
          description: `Found ${count} issue${count === 1 ? '' : 's'} preventing execution.`,
          color: 'error',
          icon: 'i-lucide-alert-circle'
        });
      }

      return res;
    } catch (err: any) {
      const message = err?.message || 'An unexpected network error occurred during validation.';
      error.value = message;
      toast.add({
        title: 'Validation Request Failed',
        description: message,
        color: 'error',
        icon: 'i-lucide-x-circle'
      });
      return null;
    } finally {
      isLoading.value = false;
    }
  }

  function reset() {
    isLoading.value = false;
    report.value = null;
    error.value = null;
  }

  return {
    isLoading,
    report,
    error,
    runValidation,
    reset
  };
}

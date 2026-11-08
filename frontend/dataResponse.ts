export async function readWorkspaceResponse(response: Response): Promise<any> {
  let data: any;
  try {
    data = await response.json();
  } catch {
    if (response.redirected) throw new Error('Your sign-in session needs attention. Reload the workspace to sign in again.');
    throw new Error('The page is available, but the workspace data connection is not ready. Make sure the Windows app and its remote connection are running, then reload.');
  }
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'The workspace request could not be completed.');
  if (!data || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).length === 0) {
    throw new Error('Workspace data is unavailable. Reload after the data connection is restored.');
  }
  return data;
}

let activeModule: string | null = null;
export const getActiveModule = () => activeModule;
export const setActiveModule = (code: string | null) => { activeModule = code; };

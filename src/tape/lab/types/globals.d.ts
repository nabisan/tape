export interface PlotlyDiv extends HTMLDivElement {
  layout?: { width?: number; [key: string]: unknown };
  _fullLayout?: { width?: number };
}

export interface PlotlyApi {
  newPlot(root: HTMLElement, data: object[], layout: object, config: object): Promise<PlotlyDiv>;
  purge(root: HTMLElement): void;
  Plots: { resize(root: HTMLElement): Promise<unknown> };
}

declare global {
  interface Window {
    Plotly?: PlotlyApi;
    __tapePlotly?: Promise<PlotlyApi>;
  }
}

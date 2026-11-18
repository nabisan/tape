export interface PlotlyDiv extends HTMLDivElement {
  layout?: { width?: number; [key: string]: unknown };
  _fullLayout?: { width?: number };
  on?(event: "plotly_relayout" | "plotly_doubleclick", handler: (event: object) => void): void;
}

export interface PlotlyApi {
  newPlot(root: HTMLElement, data: object[], layout: object, config: object): Promise<PlotlyDiv>;
  react(root: HTMLElement, data: object[], layout: object, config: object): Promise<PlotlyDiv>;
  purge(root: HTMLElement): void;
  Plots: { resize(root: HTMLElement): Promise<unknown> };
}

declare global {
  interface Window {
    Plotly?: PlotlyApi;
    __tapePlotly?: Promise<PlotlyApi>;
  }
}

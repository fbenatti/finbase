declare module 'react-pivottable/PivotTableUI' {
  import { Component } from 'react';

  interface PivotTableUIProps {
    data: any[];
    rows?: string[];
    cols?: string[];
    vals?: string[];
    aggregatorName?: string;
    rendererName?: string;
    renderers?: Record<string, any>;
    hiddenAttributes?: string[];
    hiddenFromAggregators?: string[];
    hiddenFromDragDrop?: string[];
    onChange?: (state: PivotTableUIState) => void;
    unusedOrientationCutoff?: number;
    [key: string]: any;
  }

  interface PivotTableUIState {
    data: any[];
    rows: string[];
    cols: string[];
    vals: string[];
    aggregatorName: string;
    rendererName: string;
    hiddenAttributes: string[];
    hiddenFromAggregators: string[];
    hiddenFromDragDrop: string[];
    unusedOrientationCutoff: number;
    [key: string]: any;
  }

  export default class PivotTableUI extends Component<PivotTableUIProps> {
    state: PivotTableUIState;
  }
}

declare module 'react-pivottable/TableRenderers' {
  const TableRenderers: Record<string, React.ComponentType<any>>;
  export default TableRenderers;
}

declare module 'react-pivottable/PlotlyRenderers' {
  function createPlotlyRenderers(
    PlotlyComponent: React.ComponentType<any>,
  ): Record<string, React.ComponentType<any>>;

  export default createPlotlyRenderers;
}

declare module 'react-pivottable/pivottable.css';

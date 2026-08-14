declare module 'react-pivottable/PivotTableUI' {
  import { Component, ComponentType } from 'react';

  type OrderKey = 'key_a_to_z' | 'value_a_to_z' | 'value_z_to_a';

  interface PivotTableUIProps {
    data: any[];
    rows?: string[];
    cols?: string[];
    vals?: string[];
    aggregatorName?: string;
    aggregators?: Record<string, (...args: any[]) => any>;
    rendererName?: string;
    renderers?: Record<string, ComponentType<any>>;
    valueFilter?: Record<string, Record<string, boolean>>;
    rowOrder?: OrderKey;
    colOrder?: OrderKey;
    hiddenAttributes?: string[];
    hiddenFromAggregators?: string[];
    hiddenFromDragDrop?: string[];
    derivedAttributes?: Record<string, (record: Record<string, any>) => any>;
    sorters?: Record<string, ((a: string, b: string) => number) | undefined>;
    onChange: (state: PivotTableUIProps) => void;
    unusedOrientationCutoff?: number;
    menuLimit?: number;
    tableOptions?: Record<string, any>;
    [key: string]: any;
  }

  export type { PivotTableUIProps };

  export default class PivotTableUI extends Component<PivotTableUIProps> {
    state: {
      unusedOrder: string[];
      zIndices: Record<string, number>;
      maxZIndex: number;
      openDropdown: boolean | string;
      attrValues: Record<string, Record<string, number>>;
      materializedInput: any[];
      data?: any[];
    };
  }
}

declare module 'react-pivottable/PivotTable' {
  import { Component } from 'react';
  const PivotTable: Component<any>;
  export default PivotTable;
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

declare module 'react-pivottable/Utilities' {
  export const PivotData: any;
  export function getSort(
    sorters: Record<string, ((a: string, b: string) => number) | undefined>,
    attr: string,
  ): (a: string, b: string) => number;
  export function sortAs(values: string[]): (a: string, b: string) => number;
  export const aggregators: Record<string, (...args: any[]) => any>;
  export const numberFormat: (opts: { digitsAfterDecimal?: number; scaler?: number; prefix?: string; suffix?: string; thousandsSep?: string; decimalSep?: string; showZero?: boolean }) => {
    format: (x: number) => string;
  };
}

declare module 'react-pivottable/pivottable.css';

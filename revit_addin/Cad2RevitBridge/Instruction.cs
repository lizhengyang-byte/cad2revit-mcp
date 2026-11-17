using System.Collections.Generic;

namespace Cad2RevitBridge
{
    /// <summary>
    /// 与 docs/mcp-tools.md 中 generate_revit_script 的输出一一对应。
    /// </summary>
    public class Instruction
    {
        public string ElementId { get; set; }
        public string Op { get; set; }
        public string Level { get; set; }
        public double ElevationMm { get; set; }
        public string Family { get; set; }
        public string Type { get; set; }
        public Geometry Geometry { get; set; }
        public Dictionary<string, object> Params { get; set; }
    }

    public class Geometry
    {
        public double[] Start { get; set; }
        public double[] End { get; set; }
        public double[] Insert { get; set; }
        public double[] Section { get; set; }
        public double Thickness { get; set; }
        public double Width { get; set; }
        public double Depth { get; set; }
        public double Rotation { get; set; }
    }

    public class ExecutionResult
    {
        public string ElementId { get; set; }
        public bool Ok { get; set; }
        public string RevitId { get; set; }
        /// <summary>Actual placement in millimetres (X, Y, Z), read back from Revit.</summary>
        public double[] Position { get; set; }
        public string Level { get; set; }
        public string Code { get; set; }
        public string Message { get; set; }
    }
}

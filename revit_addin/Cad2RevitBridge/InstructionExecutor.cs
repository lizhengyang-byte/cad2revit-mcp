using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace Cad2RevitBridge
{
    /// <summary>
    /// 在一个 Transaction 内执行一批指令，任何异常整批回滚后逐条返回失败。
    /// 幂等由调用方（MCP 服务）负责，这里只保证不重复创建同一 ElementId。
    /// </summary>
    public class InstructionExecutor
    {
        private readonly UIApplication _uiApp;

        public InstructionExecutor(UIApplication uiApp)
        {
            _uiApp = uiApp;
        }

        public IList<ExecutionResult> ExecuteBatch(IList<Instruction> instructions)
        {
            var results = new List<ExecutionResult>();
            var document = _uiApp.ActiveUIDocument?.Document;
            if (document == null)
            {
                return instructions.Select(i => Fail(i, "E-4001", "没有打开的 Revit 文档")).ToList();
            }

            using (var transaction = new Transaction(document, "CAD2Revit batch"))
            {
                transaction.Start();
                try
                {
                    foreach (var instruction in instructions)
                    {
                        var result = ExecuteOne(document, instruction);
                        results.Add(result);
                        if (!result.Ok)
                        {
                            transaction.RollBack();
                            MarkRemainingAsRolledBack(instructions, results, instruction);
                            return results;
                        }
                    }
                    transaction.Commit();
                    return results;
                }
                catch (Exception ex)
                {
                    if (transaction.HasStarted()) transaction.RollBack();
                    return instructions.Select(i => Fail(i, "E-4002", ex.Message)).ToList();
                }
            }
        }

        private ExecutionResult ExecuteOne(Document document, Instruction instruction)
        {
            switch (instruction.Op)
            {
                case "create_gridline":
                    return CreateGrid(document, instruction);
                case "create_column":
                    return CreateColumn(document, instruction);
                case "create_wall":
                    return CreateWall(document, instruction);
                case "create_beam":
                    return CreateBeam(document, instruction);
                case "create_level":
                    return CreateLevel(document, instruction);
                default:
                    return Fail(instruction, "E-4003", $"未知指令 {instruction.Op}");
            }
        }

        private ExecutionResult CreateGrid(Document document, Instruction instruction)
        {
            var level = FindLevel(document, instruction.Level);
            if (level == null) return LevelMissing(instruction);
            var line = Line.CreateBound(ToXyz(instruction.Geometry.Start, level.Elevation),
                                        ToXyz(instruction.Geometry.End, level.Elevation));
            var grid = Grid.Create(document, line);
            grid.Name = (instruction.Params != null && instruction.Params.ContainsKey("name"))
                ? instruction.Params["name"].ToString()
                : instruction.ElementId;
            return Success(instruction, grid.Id);
        }

        private ExecutionResult CreateColumn(Document document, Instruction instruction)
        {
            var level = FindLevel(document, instruction.Level);
            if (level == null) return LevelMissing(instruction);
            var symbol = FindSymbol(document, instruction.Family, instruction.Type);
            if (symbol == null) return Fail(instruction, "E-3001", "柱族类型未载入");

            var point = ToXyz(instruction.Geometry.Insert, level.Elevation);
            var column = document.Create.NewFamilyInstance(point, symbol, level,
                Autodesk.Revit.DB.Structure.StructuralType.Column);
            return Success(instruction, column.Id);
        }

        private ExecutionResult CreateWall(Document document, Instruction instruction)
        {
            var level = FindLevel(document, instruction.Level);
            if (level == null) return LevelMissing(instruction);
            var symbol = FindSymbol(document, instruction.Family, instruction.Type);
            if (symbol == null) return Fail(instruction, "E-3001", "墙类型未载入");

            var line = Line.CreateBound(ToXyz(instruction.Geometry.Start, level.Elevation),
                                        ToXyz(instruction.Geometry.End, level.Elevation));
            var wall = Wall.Create(document, line, symbol.Id, level.Id, 3000, 0, false, false);
            if (instruction.Geometry.Thickness > 0)
            {
                wall.Width = MmToFeet(instruction.Geometry.Thickness);
            }
            return Success(instruction, wall.Id);
        }

        private ExecutionResult CreateBeam(Document document, Instruction instruction)
        {
            var level = FindLevel(document, instruction.Level);
            if (level == null) return LevelMissing(instruction);
            var symbol = FindSymbol(document, instruction.Family, instruction.Type);
            if (symbol == null) return Fail(instruction, "E-3001", "梁族类型未载入");

            var start = ToXyz(instruction.Geometry.Start, level.Elevation);
            var end = ToXyz(instruction.Geometry.End, level.Elevation);
            var line = Line.CreateBound(start, end);
            var beam = document.Create.NewFamilyInstance(line, symbol, level,
                Autodesk.Revit.DB.Structure.StructuralType.Beam);
            return Success(instruction, beam.Id);
        }

        // Storey identity is exact: a missing level must fail loudly. The old
        // "closest elevation" fallback silently built a floor at the wrong
        // height whenever the drawing said "4F" but the model had no 4F.
        private static Level FindLevel(Document document, string name)
        {
            return new FilteredElementCollector(document)
                .OfClass(typeof(Level)).Cast<Level>()
                .FirstOrDefault(l => l.Name == name);
        }

        private static ExecutionResult LevelMissing(Instruction instruction)
        {
            return Fail(instruction, "E-3002",
                $"标高 {instruction.Level} 不存在，请先执行 create_level 或核对 level_map");
        }

        /// <summary>
        /// 按图纸标高新建标高；同名已存在时直接复用，避免重复提交时抛出。
        /// 高程来自指令的 ElevationMm，单位换算在 MmToFeet 完成。
        /// </summary>
        private ExecutionResult CreateLevel(Document document, Instruction instruction)
        {
            var existing = FindLevel(document, instruction.Level);
            if (existing != null) return Success(instruction, existing.Id);
            var level = Level.Create(document, MmToFeet(instruction.ElevationMm));
            level.Name = instruction.Level;
            return Success(instruction, level.Id);
        }

        private static FamilySymbol FindSymbol(Document document, string family, string type)
        {
            return new FilteredElementCollector(document)
                .OfClass(typeof(FamilySymbol))
                .Cast<FamilySymbol>()
                .FirstOrDefault(s => s.Family?.Name == family && s.Name == type);
        }

        private static XYZ ToXyz(double[] point, double z)
        {
            return new XYZ(MmToFeet(point[0]), MmToFeet(point[1]), z);
        }

        private static double MmToFeet(double mm) => mm / 304.8;

        private static ExecutionResult Success(Instruction instruction, ElementId id)
        {
            return new ExecutionResult
            {
                ElementId = instruction.ElementId,
                Ok = true,
                RevitId = id.IntegerValue.ToString()
            };
        }

        private static ExecutionResult Fail(Instruction instruction, string code, string message)
        {
            return new ExecutionResult
            {
                ElementId = instruction.ElementId,
                Ok = false,
                Code = code,
                Message = message
            };
        }

        private static void MarkRemainingAsRolledBack(IList<Instruction> instructions,
                                                      List<ExecutionResult> results,
                                                      Instruction failed)
        {
            var reported = new HashSet<string>(results.Select(r => r.ElementId));
            foreach (var instruction in instructions)
            {
                if (reported.Contains(instruction.ElementId)) continue;
                results.Add(Fail(instruction, "E-4002", $"批次因 {failed.ElementId} 失败已整批回滚"));
                reported.Add(instruction.ElementId);
            }
        }
    }
}

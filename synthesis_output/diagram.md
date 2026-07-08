flowchart TD
  subgraph SAHH/SAH代谢轴
    A1[SAHH活性抑制]
    A2[SAH积累]
    A3[DNA/组蛋白甲基化抑制]
    A4[促衰老基因激活]
    A5[血管/细胞衰老]
    A6[镉暴露SAH保护]
    A7[线虫AHCY-1突变]
    A8[线虫SAH/SAM上升]
    A9[线虫寿命延长]
    A10[果蝇AHCY敲低]
    A11[果蝇长寿优势消失]
  end
  A1 --负相关--> A2
  A2 --负相关--> A3
  A3 --负相关--> A4
  A4 --正相关--> A5
  A6 --矛盾负相关--> A5
  A7 --正相关--> A8
  A8 --正相关--> A9
  A10 --正相关--> A11
  A7 -.种间矛盾.-> A1
  A10 -.矛盾.-> A7
  class A1,A3,A10,A11 red
  class A2,A4,A5,A8,A9 green
  class A6 orange

  subgraph RNA_m6A修饰
    B1[METTL3下调]
    B2[p53/p21上调]
    B3[细胞衰老]
    B4[FTO随龄上调]
    B5[mRNA m6A下降]
    B6[骨骼肌缺陷]
    B7[IMP2识别m6A]
    B8[PINK1稳定]
    B9[线粒体自噬增强]
    B10[BMSC衰老减轻]
    B11[METTL3-TERC轴]
    B12[端粒酶活性]
    B13[YTHDC1读取]
    B14[端粒维持]
    B15[ALKBH5去甲基化]
    B16[肿瘤端粒长度矛盾]
  end
  B1 --负相关--> B2
  B2 --正相关--> B3
  B4 --负相关--> B5
  B5 --正相关--> B6
  B7 --正相关--> B8
  B8 --正相关--> B9
  B9 --负相关--> B10
  B11 --正相关--> B12
  B13 --正相关--> B14
  B15 --负相关--> B12
  B11 --矛盾--> B16
  class B1,B5,B6,B10 red
  class B2,B3,B4,B7,B8,B9,B11,B12,B13,B14,B15 green
  class B16 orange

  subgraph 其他腺苷甲基化
    C1[mtDNA 6mA失调]
    C2[OXPHOS缺陷]
    C3[氧化应激升高]
    C4[寿命缩短]
    C5[DIMT-1活性降低]
    C6[rRNA m6,2A降低]
    C7[寿命延长]
    C8[应激抵抗增强]
    C9[DNA 6mA时钟]
    C10[年龄预测误差低]
  end
  C1 --正相关--> C2
  C2 --正相关--> C3
  C3 --正相关--> C4
  C5 --负相关--> C6
  C6 --负相关--> C7
  C5 --负相关--> C8
  C9 --正相关--> C10
  class C1 orange
  class C2,C4,C5,C6,C10 red
  class C3,C7,C8,C9 green
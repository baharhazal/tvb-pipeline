# Diffusion processing

# 001.mgz: wait for subject folder to exist before proceeding
$(sd)/dwi/raw.mif: $(DWI) $(sd)/mri/orig/001.mgz
	mkdir -p $(sd)/dwi
	mrconvert $(raw_mif_convert_flags) -force $< $@

# register T1 to DWI image
$(sd)/dwi/t2d.mat: $(fs_done) $(sd)/dwi/bzero.nii.gz $(sd)/mri/T1.RAS.nii.gz
	flirt -ref $(sd)/dwi/bzero.nii.gz \
	    -in $(sd)/mri/T1.RAS.nii.gz \
	    -omat $(sd)/dwi/t2d.mat \
	    -out $(sd)/dwi/T1_in_bzero.nii.gz \
	    $(regopts)

# move label volume to DWI space
$(sd)/dwi/aparc_aseg.nii.gz: $(sd)/dwi/t2d.mat $(sd)/mri/$(aa).RAS.RO.nii.gz
	flirt -applyxfm -interp nearestneighbour \
	    -in $(sd)/mri/$(aa).RAS.RO.nii.gz \
	    -ref $(sd)/dwi/bzero.nii.gz \
	    -init $(sd)/dwi/t2d.mat -out $@

# preprocess DWI (TODO)
$(sd)/dwi/preproc.mif: $(sd)/dwi/raw.mif
	# dwipreproc -force -rpe_none $(pe_dir) $< $@
	# TODO reconutil func to invoke dwipreproc correctly
	cp $< $@

# extract b0 volume
$(sd)/dwi/bzero.mif: $(sd)/dwi/preproc.mif
	dwiextract -force -bzero $< $@

# estimate DWI response function
$(sd)/dwi/response.txt: $(sd)/dwi/preproc.mif
	dwi2response -nthreads $(nthread) -force tournier $< $@

# extract brain mask volume
$(sd)/dwi/mask.mif: $(sd)/dwi/preproc.mif
	dwi2mask -force $< $@

# estimate FODs
$(sd)/dwi/fod.mif: $(sd)/dwi/preproc.mif $(sd)/dwi/response.txt
	dwi2fod csd $^ $@ -force -nthreads $(nthread)

# convert FS labels to connectivity labels
$(sd)/dwi/label.mif: $(sd)/dwi/aparc_aseg.nii.gz
	labelconvert $< \
	    $(lut_fs) \
	    $(lut_mrt3_fs) \
	    $@ -force

# generate all tracks
$(sd)/dwi/all.tck: $(sd)/dwi/fod.mif $(sd)/dwi/mask.mif
	tckgen $< $@ \
	    -mask $(sd)/dwi/mask.mif \
	    -seed_image $(sd)/dwi/mask.mif \
	    -number $(ntrack) -force -nthreads $(nthread)

# subsample tracks
$(sd)/dwi/100k.tck: $(sd)/dwi/all.tck
	tckedit -number 100K $< $@

# generate track counts for connectome
$(sd)/dwi/triu_counts.txt: $(sd)/dwi/all.tck $(sd)/dwi/label.mif 
	tck2connectome $^ $@ -force -nthreads $(nthread)

# generate track average lengths for connectome
$(sd)/dwi/triu_lengths.txt: $(sd)/dwi/all.tck $(sd)/dwi/label.mif 
	tck2connectome $^ $@ -scale_length -stat_edge mean -force -nthreads $(nthread)

# convert to non-triangular, normalize, etc TODO
$(sd)/dwi/%.txt: $(sd)/dwi/triu_%.txt
	python -m util.util postprocess_connectome $< $@
